#!/bin/bash
set -e

APP_DIR="/home/zeyam/.openclaw/workspace/supermarket-dashboard"
cd "$APP_DIR"
source venv/bin/activate

echo "Stopping any existing server..."
pkill -f "python app.py" || true
fuser -k 5000/tcp || true
sleep 2

echo "Starting Flask server..."
nohup python app.py > /tmp/server.log 2>&1 &
SERVER_PID=$!
echo "Server started with PID $SERVER_PID"

# Wait for server to be ready
echo "Waiting for server to be ready..."
for i in {1..30}; do
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/ | grep -q "200"; then
        echo "Server is ready!"
        break
    fi
    sleep 1
done

# Create directory for HTML dump
mkdir -p /tmp/dashboard_html

# Fetch each page and save HTML
pages=(
    "/" "index"
    "/daily" "daily"
    "/weekly" "weekly"
    "/monthly" "monthly"
    "/about" "about"
)

for ((i=0; i<${#pages[@]}; i+=2)); do
    path=${pages[i]}
    name=${pages[i+1]}
    url="http://127.0.0.1:5000${path}"
    file="/tmp/dashboard_html/${name}.html"
    echo "Fetching $url -> $file"
    curl -s "$url" > "$file"
    # Check if we got something
    if [ -s "$file" ]; then
        echo "  Success: $(wc -c < "$file") bytes"
    else
        echo "  Warning: empty file"
    fi
done

# Stop server
echo "Stopping server..."
kill $SERVER_PID
wait $SERVER_PID 2>/dev/null || true
echo "Done. HTML files saved in /tmp/dashboard_html"