#!/bin/bash

# Start both backend and frontend servers for local development

echo "Starting TunaTale..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Check if frontend dependencies are installed
if [ ! -d "frontend/node_modules" ]; then
    echo "Error: Frontend dependencies not installed. Please run:"
    echo "  cd frontend && npm install"
    exit 1
fi

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    # Frontend: kill Vite child, then npm parent
    if [ -n "$FRONTEND_PID" ]; then
        pkill -P "$FRONTEND_PID" 2>/dev/null
        kill "$FRONTEND_PID" 2>/dev/null
    fi
    # Backend: SIGINT for clean Python shutdown (avoids semaphore leaks)
    if [ -n "$BACKEND_PID" ]; then
        pkill -INT -P "$BACKEND_PID" 2>/dev/null
        kill -INT "$BACKEND_PID" 2>/dev/null
    fi
    wait 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# Start backend in background
echo "Starting backend API on http://localhost:8000..."
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Give backend time to start
sleep 2

# Start frontend in background
echo "Starting frontend on http://localhost:5173..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "Application started!"
echo ""
echo "  Backend API:  http://localhost:8000"
echo "  API Docs:     http://localhost:8000/docs"
echo "  Frontend:     http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for both processes (suppress job-death messages)
wait 2>/dev/null
