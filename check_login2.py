from app import app

with app.test_client() as c:
    resp = c.get('/login')
    html = resp.data.decode('utf-8', errors='replace')
    # Find the progress bar section
    idx = html.find('page-progress')
    if idx >= 0:
        print('=== page-progress section ===')
        print(html[idx-50:idx+500])
    # Find the pageshow section
    idx2 = html.find('pageshow')
    if idx2 >= 0:
        print('\n=== pageshow section ===')
        print(html[idx2-100:idx2+400])
    # Check response headers
    print('\n=== Response headers ===')
    for k, v in resp.headers:
        print(f'{k}: {v}')
