import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default):
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.getenv("BCFMS_SECRET_KEY", "dev-only-insecure-key-change-before-deployment")
DEBUG = env_bool("BCFMS_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("BCFMS_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "accounts",
    "ledger",
    "cases",
    "core",
    "assistant",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.AccountPolicyMiddleware",
    "core.middleware.ActivityMiddleware",
]

ROOT_URLCONF = "bcfms.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.shell",
            ],
        },
    },
]

WSGI_APPLICATION = "bcfms.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "index"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("BCFMS_TIME_ZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "media/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8
DATA_UPLOAD_MAX_MEMORY_SIZE = 210 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "60/min"},
}

# --- BCFMS ---------------------------------------------------------------
# Key that wraps custodial wallet keys, TOTP secrets and evidence files at rest.
# Falls back to SECRET_KEY in development; set it explicitly (and keep it) in production.
BCFMS_VAULT_KEY = os.getenv("BCFMS_VAULT_KEY", SECRET_KEY)
BCFMS_CHAIN_ID = 2026
BCFMS_EVIDENCE_VAULT = MEDIA_ROOT / "vault"
BCFMS_MAX_EVIDENCE_MB = int(os.getenv("BCFMS_MAX_EVIDENCE_MB", "200"))
# Roles that must enrol an authenticator app. Enforcement is off by default so the
# seeded demo accounts can sign in; switch it on for any real deployment.
BCFMS_MFA_ROLES = ("ADMIN", "INVESTIGATOR", "FORENSIC", "JUDICIARY")
BCFMS_ENFORCE_MFA = env_bool("BCFMS_ENFORCE_MFA", False)

# --- Local LLM-RAG (isolated: weights are read from disk, no network calls) ---
def _model_path(name, default):
    path = Path(os.getenv(name, default))
    return path if path.is_absolute() else BASE_DIR / path


# Gemma 4 E2B (instruction-tuned, Q4_K_M GGUF) run in-process by llama.cpp.
BCFMS_LLM_PATH = _model_path("BCFMS_LLM_PATH", "models/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q4_K_M.gguf")
BCFMS_EMBED_PATH = _model_path("BCFMS_EMBED_PATH", "models/all-MiniLM-L6-v2")
BCFMS_LLM_ENABLED = env_bool("BCFMS_LLM_ENABLED", True)
BCFMS_LLM_GPU_LAYERS = int(os.getenv("BCFMS_LLM_GPU_LAYERS", "-1"))  # -1 = all layers on the GPU, 0 = CPU only
BCFMS_LLM_CONTEXT = int(os.getenv("BCFMS_LLM_CONTEXT", "8192"))
BCFMS_LLM_MAX_NEW_TOKENS = int(os.getenv("BCFMS_LLM_MAX_NEW_TOKENS", "450"))
BCFMS_RAG_TOP_K = int(os.getenv("BCFMS_RAG_TOP_K", "6"))
BCFMS_ASSISTANT_ROLES = ("ADMIN", "POLICE", "INVESTIGATOR")
