web: python manage.py migrate --noinput && gunicorn hal9mil.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 120
