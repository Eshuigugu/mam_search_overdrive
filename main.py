import time
import requests
import json
import os
import pickle
from appdirs import user_data_dir
import html
import csv
import argparse
from typing import List, Dict

mam_lang_code_to_overdrive = {'ENG': 'en', 'SPA': 'es'}

# this script does create some files under this directory
appname = "search_overdrive"
appauthor = "Eshuigugu"
data_dir = user_data_dir(appname, appauthor)
cookies_filepath = os.path.join(data_dir, 'cookies.pkl')
resume_id_filepath = os.path.join(data_dir, 'resume_id.txt')
data_dir = user_data_dir(appname, appauthor)

if not os.path.isdir(data_dir):
    os.makedirs(data_dir)

if os.path.exists(resume_id_filepath):
    with open(resume_id_filepath, 'r') as resume_file:
        resume_id = int(resume_file.read().strip())
else:
    resume_id = 0

sess = requests.Session()
if os.path.exists(cookies_filepath):
    cookies = pickle.load(open(cookies_filepath, 'rb'))
    sess.cookies = cookies


def parse_series_position(series_positions):
    if ',' in series_positions:
        series_positions = series_positions.strip(',').split(',')
    elif '-' in series_positions:
        series_start, series_end = series_positions.split('-', maxsplit=1)
        if series_start.isdigit() and series_end.isdigit():
            series_positions = map(str, list(range(int(series_start), int(series_end) + 1)))
    else:
        series_positions = [series_positions]
    series_positions = [x.lstrip('0') for x in series_positions if x.lstrip('0')]
    return series_positions


def search_overdrive(title, authors, mediatype, series_name=None, series_positions=None, language=None):
    # use Overdrive's autocomplete to check if the books are on their platform
    book_on_overdrive = False
    ac_queries = [title] + ([series_name] if series_name else [])
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

    # queries by title or series_name + series_position and author
    queries = {f'{title} {author}' for author in authors[:5]}
    if series_positions:
        queries |= {f'{series_name} {series_position} {author}' for series_position in
                    parse_series_position(series_positions) for author in authors}
    media_items = []
    for subdomain in overdrive_subdomains:
        od_api_url = f'https://{subdomain}.overdrive.com/rest/media'
        for query in list(queries)[:20]:  # max 20 queries per book
            params = {
                'query': query,
                'mediaTypes': mediatype,
                'includeFacets': 'false',
                'showOnlyAvailable': only_available  # can limit to only available titles
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


def input_mam_id():
    mam_id = input(f'provide mam_id: ').strip()
    headers = {"cookie": f"mam_id={mam_id}"}
    r = sess.get('https://www.myanonamouse.net/jsonLoad.php', headers=headers, timeout=5)  # test cookie
    if r.status_code != 200:
        raise Exception(f'Error communicating with API. status code {r.status_code} {r.text}')


def search_mam(title, author, lang_code=None, audiobook=False, ebook=False):
    mam_categories = []
    if audiobook:
        mam_categories.append(13)
    if ebook:
        mam_categories.append(14)
    if not mam_categories:
        return False
    params = {
        "tor": {
            "text": f"@title {title} @author {author}",  # The search string.
            "main_cat": mam_categories,
            "browse_lang": [lang_code] if lang_code else []
        },
    }
    try:
        r = sess.post('https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php', json=params)
        if r.text == '{"error":"Nothing returned, out of 0"}':
            return False
        if r.json()['total']:
            return f"https://www.myanonamouse.net/t/{r.json()['data'][0]['id']}"
    except Exception as e:
        print(f'error searching MAM {e}')
    return False


def get_mam_requests(limit: int = 10_000) -> List:
    keep_going = True
    start_idx = 0
    req_books = []

    # fetch list of requests to search for
    while keep_going:
        time.sleep(1)
        url = 'https://www.myanonamouse.net/tor/json/loadRequests.php'
        query_params = {
            'tor[text]': '',
            'tor[srchIn][title]': 'true',
            'tor[viewType]': 'unful',
            'tor[startDate]': '',
            'tor[endDate]': '',
            'tor[startNumber]': f'{start_idx}',
            'tor[sortType]': 'dateD',
        }
        if language_ints:
            query_params['tor[browse_lang][]'] = language_ints
        r = sess.get(url, params=query_params, headers={'Content-type': 'application/json; charset=utf-8'}, timeout=60)
        if r.status_code >= 300:
            print(f'error fetching requests. status code {r.status_code} {r.text}')
            if r.status_code == 403:
                input_mam_id()
                continue

        response_json = r.json()
        req_books += response_json['data']
        total_items = response_json['found']
        start_idx += response_json['perpage']
        # check that it's not returning requests already searched for
        keep_going = min(total_items, limit) > start_idx and not \
            min(book["id"] for book in req_books) <= resume_id

    # save cookies for later
    with open(cookies_filepath, 'wb') as f:
        pickle.dump(sess.cookies, f)

    req_books = {book["id"]: book for book in req_books}  # make sure there's no duplicates the list of requested books
    print(f'Got list of {len(req_books)} requested books')
    with open(resume_id_filepath, 'w') as resume_file:
        # arrange list of requests old > new
        for book_id in sorted(list(req_books)):
            book = req_books[book_id]
            # write the most recent request id
            resume_file.seek(0)
            resume_file.write(str(book["id"]))
            # edit book object
            book['url'] = f'https://www.myanonamouse.net/tor/viewRequest.php/{book["id"] / 1e5:.5f}'
            book['title'] = html.unescape(str(book['title']))
            if book['authors']:
                book['authors'] = [author for k, author in json.loads(book['authors']).items()]
            if book["id"] > resume_id:
                yield book


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
           and mam_book['torsatch'] == 0


def search_overdrive_for_mam_book(mam_book):
    mediatype = mam_book['cat_name'].split(' ')[0].rstrip('s').lower()  # will be ebook or audiobook
    series_name, series_positions = list(map(html.unescape, list(json.loads(mam_book['series']).values())[0]))\
        if mam_book['series'] else (None, None)
    return search_overdrive(mam_book['title'], mam_book['authors'], mediatype,
                            series_name=series_name, series_positions=series_positions,
                            language=mam_book["lang_code"]
                            )


def write_to_csv(csv_filepath: str, book: Dict, hits: List):
    query_str = f'{book["title"]} {book["authors"][0]}'
    goodreads_book = {}
    try:
        r = sess.get('https://www.goodreads.com/book/auto_complete', params={'format': 'json', 'q': query_str},
                     timeout=10)
        if r.status_code == 200 and r.json():
            goodreads_book = r.json()[0]
    except Exception as e:
        print('error querying goodreads', e)
    goodreads_book_url = f'https://www.goodreads.com{goodreads_book["bookUrl"]}' if "bookUrl" in goodreads_book else ""
    goodreads_num_ratings = goodreads_book.get("ratingsCount", "")

    on_mam = search_mam(book["title"], book["authors"][0],
                        ebook=book['cat_name'].startswith('Ebooks '),
                        audiobook=book['cat_name'].startswith('Audiobooks '),
                        lang_code=book["language"]
                        )
    book_data = {
        "url": book["url"],
        "title": book["title"],
        "authors": ", ".join(book["authors"]),
        "series": html.unescape(" #".join(list(json.loads(book["series"]).values())[0])) if book["series"] else "",
        "votes": book["votes"],
        "category": book["cat_name"],
        "found_urls": " ".join([hit["url"] for hit in hits]),
        "found_title": hits[0]["title"],
        "goodreads_url": goodreads_book_url,
        "num_ratings": goodreads_num_ratings,
        "on_mam": on_mam,
    }
    write_headers = not os.path.exists(csv_filepath)
    with open(csv_filepath, mode="a", newline="", errors='ignore') as csv_file:
        fieldnames = book_data.keys()
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if write_headers:
            writer.writeheader()
        writer.writerow(book_data)


def main():
    for book in filter(should_search_for_book, get_mam_requests()):
        hits = search_overdrive_for_mam_book(book)
        if hits:
            pretty_print_hits(book, hits)
            if output_file:
                write_to_csv(csv_filepath=output_file, book=book, hits=hits)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Search for books on Overdrive")
    parser.add_argument("--output_file", help="Where to output a CSV file (optional)")
    parser.add_argument("--available", help="Show only books that can be borrowed now", action="store_true")
    parser.add_argument("--languages",
                        help="List of integers for the languages of books you wish to search for, "
                             "comma seperated (optional)")
    parser.add_argument("-s", "--subdomains",
                        help="List of the Overdrive subdomains to search, comma separated. "
                             "For example, --subdomains=lapl,auckland will search "
                             "https://lapl.overdrive.com/ and https://auckland.overdrive.com/", required=True)
    parser.add_argument("--after", type=int, default=resume_id,
                        help="Filters out requests older than this request ID/timestamp in microseconds. "
                             "Set to 0 to search for all requested books (optional)")
    args = parser.parse_args()

    resume_id = args.after
    output_file = args.output_file
    overdrive_subdomains = args.subdomains.split(',')
    only_available = args.available
    language_ints = args.languages.split(',') if args.languages else None

    main()
