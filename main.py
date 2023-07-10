import time
import requests
import json
import os
import pickle
from appdirs import user_data_dir
import html

overdrive_subdomains = ['auckland', 'lapl', 'sails']
mam_lang_code_to_overdrive = {'ENG': 'en', 'SPA': 'es'}

# this script does create some files under this directory
appname = "search_overdrive"
appauthor = "Eshuigugu"
data_dir = user_data_dir(appname, appauthor)

cookies_filepath = os.path.join(data_dir, 'cookies.pkl')
mam_blacklist_filepath = os.path.join(data_dir, 'blacklisted_ids.txt')

if not os.path.isdir(data_dir):
    os.makedirs(data_dir)

if os.path.exists(mam_blacklist_filepath):
    with open(mam_blacklist_filepath, 'r') as f:
        blacklist = set([int(x.strip()) for x in f.readlines()])
else:
    blacklist = set()

sess = requests.Session()
if os.path.exists(cookies_filepath):
    cookies = pickle.load(open(cookies_filepath, 'rb'))
    sess.cookies = cookies


def parse_series_position(series_positions):
    if ',' in series_positions:
        series_positions = series_positions.strip(',').split(',')
    elif '-' in series_positions:
        series_start, series_end = series_positions.split('-')
        if series_start.isdigit() and series_end.isdigit():
            series_positions = list(range(int(series_start), int(series_end) + 1))
    else:
        series_positions = [series_positions]
    return series_positions


def search_overdrive(title, authors, mediatype, series_name_position=None, language=None):
    # use Overdrive's autocomplete to check if the books are on their platform
    book_on_overdrive = False
    ac_queries = [title] + ([series_name_position[0]] if series_name_position else [])
    for query in ac_queries:
        if book_on_overdrive:
            continue
        params = {
            'query': query,
            'maxSize': '15',
            'categorySize': '15',
            'sortBy': 'score',
            'mediaType': [
                mediatype
            ],
            # API key has been in use since 2017
            # according to https://web.archive.org/web/*/https://autocomplete.api.overdrive.com/v1/autocomplete*
            'api-key': '66d3b2fb030e46bba783b1a658705fe3',
        }
        time.sleep(1)
        r = sess.get('https://autocomplete.api.overdrive.com/v1/autocomplete', params=params)
        if r.status_code == 200:
            try:
                r_json = r.json()
                if r_json["items"]:
                    book_on_overdrive = True
            except Exception as e:
                print('error with autocomplete', e)
    if not book_on_overdrive:
        return

    queries = list({f'{query} {author}'
                    for query in [title] +
                    (([f'{series_name_position[0]} {str(pos).lstrip("0")}' for pos in
                       parse_series_position(series_name_position[1])]) if series_name_position else [])
                    for author in authors[:2]})[:20]  # search by title + series and author, max of 20 queries
    media_items = []
    for subdomain in overdrive_subdomains:
        od_api_url = f'https://{subdomain}.overdrive.com/rest/media'
        for query in queries:
            params = {
                'query': query,
                'mediaTypes': mediatype,
                'includeFacets': 'false',
                # 'showOnlyAvailable': 'true'  # can limit to only available titles
            }
            if language in mam_lang_code_to_overdrive:
                params['language'] = mam_lang_code_to_overdrive[language]
            try:
                r = sess.get(od_api_url, params=params, timeout=10)
            except requests.ConnectionError as e:
                print(f'error {e}')
                time.sleep(10)
                continue

            time.sleep(1)
            try:
                r_json = r.json()
            except json.decoder.JSONDecodeError:
                print('error loading reponse JSON', r.text)
                continue

            if r.status_code == 200 and r_json['items']:
                for media_item in r_json['items']:
                    media_item['url'] = f'https://{subdomain}.overdrive.com/media/{media_item["id"]}'
                media_items += r_json['items']
    # ensure each result is unique
    media_items = list({x['url']: x for x in media_items}.values())
    return media_items


def get_mam_requests(limit=5000):
    url = 'https://www.myanonamouse.net/tor/json/loadRequests.php'
    keepGoing = True
    start_idx = 0
    req_books = []

    # fetch list of requests to search for
    while keepGoing:
        time.sleep(1)
        headers = {}
        # fill in mam_id for first run
        # headers['cookie'] = 'mam_id='

        query_params = {
            'tor[text]': '',
            'tor[srchIn][title]': 'true',
            'tor[viewType]': 'unful',
            'tor[startDate]': '',
            'tor[endDate]': '',
            'tor[startNumber]': f'{start_idx}',
            'tor[sortType]': 'dateD'
        }
        headers['Content-type'] = 'application/json; charset=utf-8'

        r = sess.get(url, params=query_params, headers=headers, timeout=60)
        if r.status_code >= 300:
            raise Exception(f'error fetching requests. status code {r.status_code} {r.text}')

        req_books += r.json()['data']
        total_items = r.json()['found']
        start_idx += 100
        keepGoing = min(total_items, limit) > start_idx and not \
            {x['id'] for x in req_books}.intersection(blacklist)

    # save cookies for later. yum
    with open(cookies_filepath, 'wb') as f:
        pickle.dump(sess.cookies, f)

    with open(mam_blacklist_filepath, 'a') as f:
        for book in req_books:
            f.write(str(book['id']) + '\n')
            book['url'] = 'https://www.myanonamouse.net/tor/viewRequest.php/' + \
                          str(book['id'])[:-5] + '.' + str(book['id'])[-5:]
            book['title'] = html.unescape(str(book['title']))
            if book['authors']:
                book['authors'] = [author for k, author in json.loads(book['authors']).items()]
    return req_books


def pretty_print_hits(mam_book, hits):
    print(mam_book['title'])
    print(' ' * 2 + mam_book['url'])
    if len(hits) > 5:
        print(' ' * 2 + f'got {len(hits)} hits')
        print(' ' * 2 + f'showing first 5 results')
        hits = hits[:5]
    for hit in hits:
        print(' ' * 2 + hit["title"])
        print(' ' * 4 + hit['url'])
    print()


def should_search_for_book(mam_book):
    return (mam_book['cat_name'].startswith('Ebooks ') or mam_book['cat_name'].startswith('Audiobooks ')) \
           and mam_book['filled'] == 0 \
           and mam_book['torsatch'] == 0 \
           and mam_book['id'] not in blacklist


def search_for_mam_book(mam_book):
    mediatype = mam_book['cat_name'].split(' ')[0].rstrip('s').lower()  # will be ebook or audiobook
    series_name_position = list(map(html.unescape, list(json.loads(mam_book['series']).values())[0])) if mam_book['series'] else None
    return search_overdrive(mam_book['title'], mam_book['authors'], mediatype,
                            series_name_position=series_name_position,
                            language=mam_book["lang_code"]
                            )


def main():
    global blacklist
    blacklist = set()
    req_books = get_mam_requests()
    for book in filter(should_search_for_book, req_books):
        hits = search_for_mam_book(book)
        with open('overdrive2.json', 'a') as f:
            f.write(json.dumps([book, hits]) + '\n')
        if hits:
            pretty_print_hits(book, hits)


if __name__ == '__main__':
    main()

