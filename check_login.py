from app import app

with app.test_client() as c:
    resp = c.get('/login')
    html = resp.data.decode('utf-8', errors='replace')
    print('Status:', resp.status_code)
    print('Has form:', '<form' in html)
    print('Has csrf_token:', 'csrf_token' in html)
    print('Has page-progress:', 'page-progress' in html)
    print('Has pageshow listener:', 'pageshow' in html)
    print('Has plotly script tag:', '/assets/plotly.min.js' in html)
    print('Response size:', len(html))
    # Check for redirect in response
    print('Location header:', resp.headers.get('Location', 'none'))

    # Also test POST with bad credentials
    resp2 = c.post('/login', data={'email': 'test@test.com', 'password': 'wrongpass', 'csrf_token': 'x'})
    print('POST status:', resp2.status_code)
    print('POST location:', resp2.headers.get('Location', 'none'))
