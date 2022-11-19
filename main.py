import time
import requests
import json
from bs4 import BeautifulSoup
import os
import pickle
from appdirs import user_data_dir

# this script does create some files under this directory
appname = "search_overdrive"
appauthor = "Eshuigugu"
data_dir = user_data_dir(appname, appauthor)

overdrive_subdomains = ['lapl', 'hcpl', 'nypl']

if not os.path.isdir(data_dir):
    os.makedirs(data_dir)
sess_filepath = os.path.join(data_dir, 'session.pkl')

mam_blacklist_filepath = os.path.join(data_dir, 'blacklisted_ids.txt')
if os.path.exists(mam_blacklist_filepath):
    with open(mam_blacklist_filepath, 'r') as f:
        blacklist = set([int(x.strip()) for x in f.readlines()])
else:
    blacklist = set()

if os.path.exists(sess_filepath):
    sess = pickle.load(open(sess_filepath, 'rb'))
    # only take the cookies
    cookies = sess.cookies
    sess = requests.Session()
    sess.cookies = cookies
else:
    sess = requests.Session()


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


def search_overdrive(title, authors, mediatype, series_name_position=None):
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
                # 'showOnlyAvailable': 'true'  # can limit to only available titles
            }
            try:
                r = sess.get(od_api_url, params=params, timeout=10)
            except requests.ConnectionError as e:
                print(f'error {e}')
                time.sleep(10)
                continue

            time.sleep(1)
            r_json = r.json()
            if r.status_code == 200 and r_json['items']:
                for media_item in r_json['items']:
                    media_item['url'] = f'https://{subdomain}.overdrive.com/media/{media_item["id"]}'
                media_items += r_json['items']
    # ensure each result is unique
    media_items = list({x['url']: x for x in media_items}.values())
    return media_items


def get_mam_requests(limit=5000):
    keepGoing = True
    start_idx = 0
    req_books = []

    # fetch list of requests to search for
    while keepGoing:
        time.sleep(1)
        url = 'https://www.myanonamouse.net/tor/json/loadRequests.php'
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

    # saving the session lets you reuse the cookies returned by MAM which means you won't have to manually update the mam_id value as often
    with open(sess_filepath, 'wb') as f:
        pickle.dump(sess, f)

    with open(mam_blacklist_filepath, 'a') as f:
        for book in req_books:
            f.write(str(book['id']) + '\n')
            book['url'] = 'https://www.myanonamouse.net/tor/viewRequest.php/' + \
                          str(book['id'])[:-5] + '.' + str(book['id'])[-5:]
            book['title'] = BeautifulSoup(book["title"], features="lxml").text
            book['authors'] = [author for k, author in json.loads(book['authors']).items()]
    return req_books


def main():
    req_books = get_mam_requests()

    req_books_reduced = [x for x in req_books if
                         (x['cat_name'].startswith('Ebooks ') or x['cat_name'].startswith('Audiobooks '))
                         and x['filled'] == 0
                         and x['torsatch'] == 0
                         and x['id'] not in blacklist]
    for book in req_books_reduced:
        mediatype = book['cat_name'].split(' ')[0].rstrip('s')  # will be ebook or audiobook
        hits = []
        hits += search_overdrive(book['title'], book['authors'], mediatype,
                                 series_name_position=list(json.loads(book['series']).values())[0] if book[
                                     'series'] else None)
        if hits:
            print(book['title'])
            print(' ' * 2 + book['url'])
            if len(hits) > 5:
                print(' ' * 2 + f'got {len(hits)} hits')
                print(' ' * 2 + f'showing first 5 results')
                hits = hits[:5]
            for hit in hits:
                print(' ' * 2 + hit['title'])
                print(' ' * 4 + hit['url'])
            print()


if __name__ == '__main__':
    main()
