#!/bin/bash

echo "🏋️  Starting Workout Tracker..."
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt --break-system-packages

echo ""
echo "Starting server..."
echo "Access the app by clicking the 'Open in Browser' notification or check the Ports tab"
echo ""

python -m uvicorn app:app --reload --host 0.0.0.0 --port 8000