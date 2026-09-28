import os
import sys

# Ensure root directory is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from app import app

# WSGI Middleware to fix Vercel route rewrites
class VercelPathMiddleware:
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        forwarded_uri = environ.get('HTTP_X_FORWARDED_URI') or environ.get('HTTP_X_MATCHED_PATH')
        if forwarded_uri and not forwarded_uri.startswith('/api/index'):
            environ['PATH_INFO'] = forwarded_uri.split('?')[0]
        elif path in ('/api/index', '/api/index.py', '/api/index/'):
            environ['PATH_INFO'] = '/'
        return self.wsgi_app(environ, start_response)

app.wsgi_app = VercelPathMiddleware(app.wsgi_app)
