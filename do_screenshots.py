import os
import time
import subprocess
import sys
import signal
from datetime import datetime

def main():
    # Change to the project directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Start Flask app
    venv_python = os.path.join('venv', 'bin', 'python')
    env = os.environ.copy()
    env['FLASK_APP'] = 'app.py'
    # We'll run the app and capture its output to a log file
    with open('flask.log', 'w') as log_file:
        proc = subprocess.Popen([venv_python, 'app.py'], stdout=log_file, stderr=subprocess.STDOUT, env=env)
    
    try:
        # Wait for the server to start
        max_wait = 30
        start = time.time()
        while time.time() - start < max_wait:
            # Check if the process is still running
            if proc.poll() is not None:
                print("Flask app exited early")
                with open('flask.log', 'r') as f:
                    print(f.read())
                return 1
            # Try to connect
            try:
                import urllib.request
                resp = urllib.request.urlopen('http://127.0.0.1:5000/', timeout=2)
                if resp.getcode() == 200:
                    break
            except:
                time.sleep(1)
        else:
            print("Server did not start in time")
            with open('flask.log', 'r') as f:
                print(f.read())
            proc.terminate()
            proc.wait()
            return 1
        
        # Now take screenshots for each route
        routes = [
            ('index', '/'),
            ('daily', '/daily'),
            ('weekly', '/weekly'),
            ('monthly', '/monthly'),
            ('about', '/about')
        ]
        
        for label, path in routes:
            # Open a new tab for this route
            subprocess.run(['openclaw', 'browser', 'action=open', f'url=http://127.0.0.1:5000{path}', f'label={label}'], check=False)
            # Wait for the page to load
            time.sleep(3)
            # Take a snapshot
            outfile = f'/tmp/screenshot_{label}.png'
            subprocess.run(['openclaw', 'browser', 'action=snapshot', f'targetId={label}', f'--output-file={outfile}'], check=False)
            time.sleep(1)
            if os.path.exists(outfile):
                print(f"Saved {outfile}")
            else:
                print(f"Failed to save {outfile}")
                # Try an alternative: snapshot by URL
                outfile2 = f'/tmp/screenshot_{label}_url.png'
                subprocess.run(['openclaw', 'browser', 'action=snapshot', f'url=http://127.0.0.1:5000{path}', f'--output-file={outfile2}'], check=False)
                if os.path.exists(outfile2):
                    print(f"Saved {outfile2} via URL")
                else:
                    print(f"Failed to save {outfile2} via URL")
    finally:
        # Kill the Flask app
        proc.terminate()
        proc.wait()
    return 0

if __name__ == '__main__':
    sys.exit(main())