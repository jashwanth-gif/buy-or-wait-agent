"""
Vercel Serverless Function entrypoint for the Buy or Wait Agent.
Bridges requests to server.AgentHandler.
"""
import os
import sys

# Ensure project root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server import AgentHandler, handler, app, application
