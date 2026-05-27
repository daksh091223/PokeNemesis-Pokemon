import os
import psycopg2
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'  # Change this!

# Database connection settings (adjust if needed)
DB_CONFIG = {
    'dbname': 'PokemonDatabase',
    'user': 'postgres',
    'password': 'Yash@1234',
    'host': 'localhost',
    'port': '5432'
}

def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)

# ---------- Helper: login_required decorator ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            # For API routes return 401, for page routes redirect
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not logged in'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ---------- Routes for pages ----------
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# ---------- API routes ----------
@app.route('/api/user', methods=['GET'])
@login_required
def api_user():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE id = %s", (session['user_id'],))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({'error': 'User not found'}), 404
    return jsonify({'user_id': session['user_id'], 'username': row[0]})

@app.route('/api/pokemon', methods=['GET'])
@login_required
def api_pokemon():
    """Return list of all Pokémon with id and name (ordered by id)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM pokemon ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{'id': r[0], 'name': r[1]} for r in rows])

@app.route('/api/teams', methods=['GET', 'POST'])
@login_required
def api_teams():
    conn = get_db_connection()
    cur = conn.cursor()
    if request.method == 'GET':
        cur.execute("""
            SELECT id, name, pokemon_ids FROM teams
            WHERE user_id = %s ORDER BY id
        """, (session['user_id'],))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([{
            'id': r[0],
            'name': r[1],
            'pokemon_ids': r[2]  # already a list (PostgreSQL array)
        } for r in rows])
    else:  # POST – create new team
        data = request.get_json()
        name = data.get('name')
        pokemon_ids = data.get('pokemon_ids')  # list of 6 ints
        if not name or not pokemon_ids or len(pokemon_ids) != 6:
            return jsonify({'error': 'Invalid team data'}), 400
        # Check team count limit (max 10)
        cur.execute("SELECT COUNT(*) FROM teams WHERE user_id = %s", (session['user_id'],))
        count = cur.fetchone()[0]
        if count >= 10:
            cur.close()
            conn.close()
            return jsonify({'error': 'You already have 10 teams. Delete one first.'}), 400
        # Insert — use DEFAULT for id (SERIAL)
        cur.execute("""
            INSERT INTO teams (user_id, name, pokemon_ids)
            VALUES (%s, %s, %s)
        """, (session['user_id'], name, pokemon_ids))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True}), 201

@app.route('/api/teams/<int:team_id>', methods=['DELETE'])
@login_required
def api_delete_team(team_id):
    conn = get_db_connection()
    cur = conn.cursor()
    # Ensure team belongs to current user
    cur.execute("DELETE FROM teams WHERE id = %s AND user_id = %s", (team_id, session['user_id']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/nemesis', methods=['POST'])
@login_required
def api_nemesis():
    data = request.get_json()
    opponent_ids = data.get('team')
    if not opponent_ids or len(opponent_ids) != 6:
        return jsonify({'error': 'Team must contain exactly 6 Pokémon IDs'}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    # Fetch types for all Pokémon (for quick lookup)
    cur.execute("""
        SELECT pt.pokemon_id, t.id, t.name
        FROM pokemon_types pt
        JOIN types t ON pt.type_id = t.id
    """)
    pokemon_types = {}
    for pid, tid, tname in cur.fetchall():
        if pid not in pokemon_types:
            pokemon_types[pid] = []
        pokemon_types[pid].append(tid)

    # Fetch type effectiveness mapping
    cur.execute("SELECT atk_id, def_id, multiplier FROM type_effectiveness")
    effectiveness = {}
    for atk, dfn, mul in cur.fetchall():
        effectiveness[(atk, dfn)] = mul

    # Fetch all available pokemon IDs once (not inside the loop!)
    cur.execute("SELECT id FROM pokemon ORDER BY id")
    all_pokemon_ids = [row[0] for row in cur.fetchall()]

    # For each opponent, find the best counter based on type effectiveness
    counter_scores = {}
    for opp_id in opponent_ids:
        opp_types = pokemon_types.get(opp_id, [])
        best_score = -1
        best_pokemon = None
        for pid in all_pokemon_ids:
            if pid in opponent_ids:
                continue
            attacker_types = pokemon_types.get(pid, [])
            if not attacker_types or not opp_types:
                continue
            total = 0
            for atk_type in attacker_types:
                for def_type in opp_types:
                    mul = effectiveness.get((atk_type, def_type), 1.0)
                    total += mul
            avg = total / (len(attacker_types) * len(opp_types))
            if avg > best_score:
                best_score = avg
                best_pokemon = pid
        if best_pokemon:
            counter_scores[best_pokemon] = counter_scores.get(best_pokemon, 0) + best_score

    # Select top 6 unique counters
    top_counters = sorted(counter_scores.items(), key=lambda x: x[1], reverse=True)
    nemesis_ids = [pid for pid, _ in top_counters[:6]]

    # Fill to 6 if needed (fallback to first available non-opponent)
    fallback_pool = [pid for pid in all_pokemon_ids if pid not in opponent_ids and pid not in nemesis_ids]
    while len(nemesis_ids) < 6 and fallback_pool:
        nemesis_ids.append(fallback_pool.pop(0))

    cur.close()
    conn.close()

    return jsonify({'nemesis_team': nemesis_ids})

# ---------- Authentication routes ----------
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Missing username or password'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if user and check_password_hash(user[1], password):
        session['user_id'] = user[0]
        return jsonify({'success': True})
    else:
        return jsonify({'error': 'Invalid username or password'}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Missing username or password'}), 400
    if len(password) < 4:
        return jsonify({'error': 'Password must be at least 4 characters'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        hashed = generate_password_hash(password)
        cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, hashed))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'success': True}), 201
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        return jsonify({'error': 'Username already exists'}), 409

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# ---------- Run the app ----------
if __name__ == '__main__':
    app.run(debug=True)