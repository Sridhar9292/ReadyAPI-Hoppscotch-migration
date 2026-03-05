try:
    import fastapi
    print("fastapi:", fastapi.__version__)
except ImportError:
    print("fastapi: NOT INSTALLED")

try:
    import uvicorn
    print("uvicorn:", uvicorn.__version__)
except ImportError:
    print("uvicorn: NOT INSTALLED")

try:
    import multipart
    print("python-multipart: OK")
except ImportError:
    print("python-multipart: NOT INSTALLED")

try:
    import dotenv
    print("python-dotenv: OK")
except ImportError:
    print("python-dotenv: NOT INSTALLED")
