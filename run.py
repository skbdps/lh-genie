"""
run.py — Start the LH Genie server.
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8501,
        reload=True,
        log_level="info",
    )
