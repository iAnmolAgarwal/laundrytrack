#!/bin/bash
# Start LaundryTrack. Usage: ./run.sh   (then open http://127.0.0.1:8000)
cd "$(dirname "$0")"
.venv/bin/python manage.py migrate --noinput >/dev/null
IP=$(ipconfig getifaddr en0 2>/dev/null)
echo "Laptop:  http://127.0.0.1:8000"
[ -n "$IP" ] && echo "Phone (same Wi-Fi):  http://$IP:8000"
exec .venv/bin/python manage.py runserver 0.0.0.0:8000
