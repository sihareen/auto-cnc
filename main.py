#!/usr/bin/env python3.14
"""
Auto CNC Drill System - Main Entry Point
"""
import uvicorn
from src.ui.server import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000) 
