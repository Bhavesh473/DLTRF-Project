import os
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import requests
import json
import time
import threading
from datetime import datetime

app = Flask(__name__, static_folder='static', template_folder='static')
app.config['SECRET_KEY'] = 'replay-secret-2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Config
CONTROL_API_URL = os.getenv("CONTROL_API_URL", "http://127.0.0.1:8000")
REPLAY_TOKEN = os.getenv("REPLAY_TOKEN", "mysecret")

# Global state
current_replay_status = {
    'running': False,
    'replay_id': None,
    'progress': 0,
    'events_processed': 0,
    'bugs_detected': 0,
    'elapsed': 0
}
session_history = []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/health')
def health():
    """Health check endpoint"""
    try:
        response = requests.get(f"{CONTROL_API_URL}/health", timeout=2)
        if response.status_code == 200:
            data = response.json()
            return jsonify({
                'api_connected': True,
                'redis_connected': data.get('redis') == 'connected'
            })
    except:
        pass
    return jsonify({'api_connected': False, 'redis_connected': False})

@app.route('/api/start', methods=['POST'])
def start_replay():
    try:
        data = request.json
        print(f"📥 Received start request: {data}")
        
        response = requests.post(
            f"{CONTROL_API_URL}/replay/start",
            json={
                'mode': data.get('mode', 'dry-run'),
                'speed': float(data.get('speed', 1.0))
            },
            headers={'Authorization': f'Bearer {REPLAY_TOKEN}'},
            timeout=10
        )
        
        print(f"📡 API Response: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            current_replay_status['running'] = True
            current_replay_status['replay_id'] = result['replay_id']
            current_replay_status['progress'] = 0
            current_replay_status['events_processed'] = 0
            current_replay_status['bugs_detected'] = 0
            print(f"✅ Replay started: {result['replay_id']}")
            return jsonify(result)
        else:
            print(f"❌ API Error: {response.text}")
            return jsonify({'error': response.text}), response.status_code
    except Exception as e:
        print(f"❌ Exception: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop', methods=['POST'])
def stop_replay():
    try:
        if not current_replay_status['replay_id']:
            return jsonify({'error': 'No active replay'}), 400
        
        response = requests.post(
            f"{CONTROL_API_URL}/replay/stop",
            json={'replay_id': current_replay_status['replay_id']},
            headers={'Authorization': f'Bearer {REPLAY_TOKEN}'},
            timeout=10
        )
        
        if response.status_code == 200:
            current_replay_status['running'] = False
            return jsonify(response.json())
        else:
            return jsonify({'error': response.text}), response.status_code
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status')
def get_status():
    return jsonify(current_replay_status)

@app.route('/api/history')
def get_history():
    return jsonify(session_history)

@app.route('/api/export')
def export_report():
    """Export replay report"""
    try:
        report = {
            'current_status': current_replay_status,
            'history': session_history,
            'exported_at': datetime.now().isoformat()
        }
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Background polling thread
def status_polling_thread():
    """Poll and emit updates"""
    while True:
        try:
            if current_replay_status['running'] and current_replay_status['replay_id']:
                response = requests.get(
                    f"{CONTROL_API_URL}/replay/status",
                    params={'replay_id': current_replay_status['replay_id']},
                    headers={'Authorization': f'Bearer {REPLAY_TOKEN}'},
                    timeout=5
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Force emit every poll
                    socketio.emit('update', {
                        'progress': data.get('progress', 0),
                        'events_processed': data.get('events_processed', 0),
                        'bugs_detected': data.get('bugs_detected', 0),
                        'elapsed': data.get('elapsed_seconds', 0),
                        'current_event': str(data.get('current_event_details', {})),
                        'event_type': 'info'
                    })
                    
                    print(f"📤 Emitted: {data.get('events_processed')} events")
                    
                    # Check completion
                    if data.get('progress', 0) >= 1.0:
                        current_replay_status['running'] = False
                        socketio.emit('completed')
                        print("✅ Replay completed")
                        
        except Exception as e:
            print(f"Poll error: {e}")
        
        time.sleep(0.5)  # Poll every 0.5s

# Start polling thread
polling_thread = threading.Thread(target=status_polling_thread, daemon=True)
polling_thread.start()

if __name__ == '__main__':
    print("🚀 Dashboard server starting on http://localhost:8050")
    socketio.run(app, host='0.0.0.0', port=8050, debug=False, allow_unsafe_werkzeug=True)