#!/bin/bash
# Double-click this file in Finder to start the app.
# Keep the Terminal window open while Streamlit is running.

APP_DIR="/Users/owenpatry/Documents/MTG Spec Engine/mtg_spec_engine"
PORT=8501
URL="http://localhost:${PORT}"

cd "$APP_DIR" || {
  echo "ERROR: Cannot find app folder:"
  echo "  $APP_DIR"
  read -r -p "Press Enter to close..."
  exit 1
}

VENV_ACTIVATE="$APP_DIR/venv/bin/activate"
STREAMLIT="$APP_DIR/venv/bin/streamlit"

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "Virtual environment not found."
  echo ""
  echo "Run once in Terminal:"
  echo "  cd \"$APP_DIR\""
  echo "  python3 -m venv venv"
  echo "  source venv/bin/activate"
  echo "  pip install -r requirements.txt"
  read -r -p "Press Enter to close..."
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV_ACTIVATE"

if [[ ! -x "$STREAMLIT" ]]; then
  echo "ERROR: streamlit not found in venv."
  echo "  $STREAMLIT"
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "MTG Spec Engine"
echo "==============="
echo ""

if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use — stopping the old Streamlit so code updates load."
  lsof -tiTCP:"$PORT" -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo "Starting Streamlit on $URL"
echo "Leave this window open while you use the app."
echo "(Press Ctrl+C here to stop the server.)"
echo ""

# macOS: open browser once the server is up
( sleep 2 && open "$URL" ) &

if ! "$STREAMLIT" run app.py --server.port "$PORT"; then
  echo ""
  echo "Streamlit exited with an error (see messages above)."
  read -r -p "Press Enter to close..."
  exit 1
fi
