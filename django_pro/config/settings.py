from pathlib import Path
import os
import sys

from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file():
    """Load repo-root .env when present; never override vars already in the process env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (
        BASE_DIR.parent / '.env',
        BASE_DIR / '.env',
        Path('/app/.env'),
    ):
        if path.is_file():
            load_dotenv(path, override=False)
            return


_load_env_file()

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', 'False').lower() in ('1', 'true', 'yes')

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG or 'test' in sys.argv:
        SECRET_KEY = 'django-insecure-local-dev-and-test-only'
    else:
        raise ImproperlyConfigured(
            'SECRET_KEY must be set in the environment or .env when DEBUG is False.'
        )


def _allowed_hosts_from_env():
    raw = os.getenv('ALLOWED_HOSTS') or 'localhost,127.0.0.1'
    hosts = []
    for part in raw.split(','):
        host = part.strip()
        if not host or host == '*':
            continue
        if host.startswith('*.'):
            host = '.' + host[2:]
        hosts.append(host)
    return hosts


ALLOWED_HOSTS = _allowed_hosts_from_env()


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'landing',
    'users.apps.UsersConfig',
    'studata',
    'dataexploration',
    'nextyearlist.apps.NextYearListConfig',
    'placementcards',
    'mathr.apps.MathrConfig',
    'coursecatalog.apps.CoursecatalogConfig',
    'desi.apps.DesiConfig',
    'dataviz.apps.DatavizConfig',
    'ccgi.apps.CcgiConfig',
    'dataLoader.apps.DataloaderConfig',
    'clickhouse_utils',
    # Allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
]

MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'clickhouse_utils.middleware.AnalyticsTimingMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.middleware.ActiveRoleMiddleware',
    'users.middleware.RedirectForbiddenToHomeMiddleware',
    'users.middleware.UsageTrackingMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Allauth middleware
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = 'config.urls'

_TEMPLATE_CONTEXT_PROCESSORS = [
    'django.template.context_processors.request',
    'django.contrib.auth.context_processors.auth',
    'django.contrib.messages.context_processors.messages',
    'users.context_processors.navigation_pages',
]

if DEBUG:
    TEMPLATES = [
        {
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [BASE_DIR / 'templates'],
            'APP_DIRS': True,
            'OPTIONS': {
                'context_processors': _TEMPLATE_CONTEXT_PROCESSORS,
            },
        },
    ]
else:
    # Production: cache compiled templates so requests do not re-read/re-compile.
    TEMPLATES = [
        {
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [BASE_DIR / 'templates'],
            'APP_DIRS': False,
            'OPTIONS': {
                'loaders': [
                    (
                        'django.template.loaders.cached.Loader',
                        [
                            'django.template.loaders.filesystem.Loader',
                            'django.template.loaders.app_directories.Loader',
                        ],
                    ),
                ],
                'context_processors': _TEMPLATE_CONTEXT_PROCESSORS,
            },
        },
    ]

WSGI_APPLICATION = 'config.wsgi.application'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SITE_ID = int(os.getenv('SITE_ID', '1'))

# Allauth Configuration
ACCOUNT_DEFAULT_HTTP_PROTOCOL = os.getenv('ACCOUNT_DEFAULT_HTTP_PROTOCOL', 'https' if not DEBUG else 'http')
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'none' 
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# Google OAuth Configuration
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'OAUTH_PKCE_ENABLED': True,
        'APP': {
            'client_id': os.getenv('GOOGLE_CLIENT_ID'),
            'secret': os.getenv('GOOGLE_CLIENT_SECRET'),
        }
    }
}
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_ADAPTER = 'users.adapters.MySocialAccountAdapter'



# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': os.getenv('POSTGRES_HOST'),
        'PORT': os.getenv('POSTGRES_PORT'),
        # Reuse DB connections across requests instead of reconnecting per request.
        'CONN_MAX_AGE': int(os.getenv('CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
    },
    'clickhouse': {
        'ENGINE': 'django.db.backends.dummy',
        'NAME': os.getenv('CLICKHOUSE_DB', 'fastapi_clickhouse_db'),
        'USER': os.getenv('CLICKHOUSE_USER', 'clickhouse_user'),
        'PASSWORD': os.getenv('CLICKHOUSE_PASSWORD', ''),
        'HOST': os.getenv('CLICKHOUSE_HOST', 'clickhouse'),
        'PORT': os.getenv('CLICKHOUSE_NATIVE_PORT', '9000'),
    },
}

# ClickHouse Analytics Configuration
USE_CLICKHOUSE_ANALYTICS = os.getenv('USE_CLICKHOUSE_ANALYTICS', 'True').lower() == 'true'
CLICKHOUSE_HOST = os.getenv('CLICKHOUSE_HOST', 'clickhouse')
CLICKHOUSE_DB = os.getenv('CLICKHOUSE_DB', 'fastapi_clickhouse_db')
CLICKHOUSE_USER = os.getenv('CLICKHOUSE_USER', 'clickhouse_user')
CLICKHOUSE_PASSWORD = os.getenv('CLICKHOUSE_PASSWORD', '')
CLICKHOUSE_PORT = int(os.getenv('CLICKHOUSE_PORT', '8123'))
CLICKHOUSE_NATIVE_PORT = int(os.getenv('CLICKHOUSE_NATIVE_PORT', '9000'))

# Database router: ClickHouse is read-only for analytics queries only.
# All migrations and writes stay on PostgreSQL.
DATABASE_ROUTERS = ['clickhouse_utils.router.AnalyticsRouter']

if not DEBUG and 'test' not in sys.argv:
    for _env_name in (
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_HOST',
    ):
        if not os.getenv(_env_name):
            raise ImproperlyConfigured(
                f'{_env_name} must be set in the environment or .env for production.'
            )


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'



# Security settings
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True'
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'True') == 'True'
CSRF_COOKIE_SECURE = os.getenv('CSRF_COOKIE_SECURE', 'True') == 'True'
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'SAMEORIGIN'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# HSTS Settings
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Proxy settings (for Nginx)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Static files (already configured in base settings.py)
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / "templates" / "static",
]
STATIC_ROOT = '/app/django_pro/files/staticfiles'

# Serving under gunicorn (no runserver auto-serving, no separate web server
# in front) — whitenoise serves static files directly from the WSGI app.
# USE_FINDERS serves straight from STATICFILES_DIRS/app static dirs, same as
# runserver did, without depending on collectstatic having been run first.
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = '/app/django_pro/files/mediafiles'

# Student photos named {ssid}.jpg (repo-root pictures/, gitignored)
STUDENT_PICTURES_DIR = Path(os.getenv('STUDENT_PICTURES_DIR') or (BASE_DIR.parent / 'pictures'))
AUTH_USER_MODEL = 'users.User'

# Redis Cache Configuration (Required for performance)
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.getenv('REDIS_URL', 'redis://redis:6379/0'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
        },
        'KEY_PREFIX': 'dsdata',
        'TIMEOUT': 300,
    }
}

# Store sessions in the cache (backed by the DB) so authenticated requests do
# not hit PostgreSQL for every session read.
SESSION_ENGINE = os.getenv(
    'SESSION_ENGINE', 'django.contrib.sessions.backends.cached_db')
SESSION_CACHE_ALIAS = 'default'

# In production, pre-compress static files at collectstatic time. Compressed
# (non-manifest) storage has no missing-manifest failure mode if collectstatic
# did not run. Async server support is not used here.
if not DEBUG:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
        },
    }

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Structured analytics timing logs. AnalyticsTimingMiddleware and the
# ClickHouse executor/query layer emit INFO records; without this
# configuration the root logger's default WARNING level drops them and there
# is no visibility into hot-path latency. Level is tunable via env so
# production can dial it back without a deploy.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'analytics': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'analytics',
        },
    },
    'loggers': {
        'analytics.timing': {
            'handlers': ['console'],
            'level': os.getenv('ANALYTICS_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'clickhouse_utils': {
            'handlers': ['console'],
            'level': os.getenv('ANALYTICS_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

# Allow larger POST bodies for chart rendering (rows payload)
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024  # 50 MB

# Disable migrations during unit testing to avoid raw SQL dependencies
if 'test' in sys.argv:
    class DisableMigrations:
        def __contains__(self, item):
            return True
        def __getitem__(self, item):
            return None
    MIGRATION_MODULES = DisableMigrations()
    SECURE_SSL_REDIRECT = False
    # Persistent connections must be disabled during tests so the test runner
    # can drop the test database (lingering connections would block teardown).
    DATABASES['default']['CONN_MAX_AGE'] = 0
