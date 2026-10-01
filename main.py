"""Entry point: `python main.py` locally and on any host."""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    production = os.getenv("ENV") == "production"
    uvicorn.run(
        "app.main:app",
        # Hosts like Render must reach the app from outside the container.
        host="0.0.0.0" if production else "127.0.0.1",
        # Render/Railway/Fly assign the port via $PORT.
        port=int(os.getenv("PORT", "8000")),
        # Trust the host's proxy so the app knows requests arrived over https.
        proxy_headers=True,
        forwarded_allow_ips="*",
        reload=not production,
    )
