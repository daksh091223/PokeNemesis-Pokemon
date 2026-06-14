import os
import psycopg2
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

DB_CONFIG = {
    'dbname': 'PokemonDatabase',
    'user': 'postgres',
    'password': 'abcd1234',
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
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not logged in'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ---------- Page routes ----------
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
            'pokemon_ids': r[2]
        } for r in rows])
    else:
        data = request.get_json()
        name = data.get('name')
        pokemon_ids = data.get('pokemon_ids')
        if not name or not pokemon_ids or len(pokemon_ids) != 6:
            return jsonify({'error': 'Invalid team data'}), 400
        cur.execute("SELECT COUNT(*) FROM teams WHERE user_id = %s", (session['user_id'],))
        count = cur.fetchone()[0]
        if count >= 10:
            cur.close()
            conn.close()
            return jsonify({'error': 'You already have 10 teams. Delete one first.'}), 400
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
    cur.execute("DELETE FROM teams WHERE id = %s AND user_id = %s", (team_id, session['user_id']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


# =============================================================
# SHARED HELPER — loads all pokemon data for both GA and RL
# =============================================================
def load_all_pokemon_data(opponent_ids):
    """
    Loads all pokemon + types + moves from DB.
    Returns (all_pokemon, opponent_team) as nemesisGA.Pokemon objects.
    Used by both /api/nemesis/ga and /api/nemesis/rl routes.
    """
    from nemesisGA import Pokemon, Move

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT id, name, hp, atk, def, sp_atk, sp_def, speed FROM pokemon")
    pokemon_rows = cur.fetchall()

    cur.execute("""
        SELECT pt.pokemon_id, t.name
        FROM pokemon_types pt
        JOIN types t ON pt.type_id = t.id
    """)
    p_types = {}
    for pid, tname in cur.fetchall():
        p_types.setdefault(pid, []).append(tname)

    move_rows = []
    try:
        cur.execute("""
            SELECT pm.pokemon_id, m.name, m.power, t.name, m.accuracy
            FROM pokemon_moves pm
            JOIN moves m ON pm.move_id = m.id
            JOIN types t ON m.type_id = t.id
        """)
        move_rows = cur.fetchall()
    except psycopg2.Error:
        conn.rollback()

    p_moves = {}
    for pid, m_name, base_power, m_type, accuracy in move_rows:
        p_moves.setdefault(pid, []).append(Move(m_name, base_power, m_type, accuracy))

    cur.close()
    conn.close()

    all_pokemon = []
    opponent_team = []
    opponent_id_set = set(opponent_ids)

    for row in pokemon_rows:
        pid, name, hp, atk, defense, sp_atk, sp_def, speed = row
        types = p_types.get(pid, ['normal'])
        learnset = p_moves.get(pid, [])
        # Fallback — give STAB move if no moves in DB
        if not learnset:
            for t in types:
                learnset.append(Move(f"{t} Strike", 90, t, 100))
        p = Pokemon(
            id=pid, name=name, types=types,
            hp=hp, attack=atk, defense=defense,
            sp_atk=sp_atk, sp_def=sp_def, speed=speed,
            learnset=learnset
        )
        all_pokemon.append(p)
        if pid in opponent_id_set:
            opponent_team.append(p)

    return all_pokemon, opponent_team


# =============================================================
# ROUTE 1 — Genetic Algorithm Nemesis  →  /api/nemesis/ga
# =============================================================
@app.route('/api/nemesis/ga', methods=['POST'])
@login_required
def api_nemesis_ga():
    data = request.get_json()
    opponent_ids = data.get('team')
    if not opponent_ids or len(opponent_ids) != 6:
        return jsonify({'error': 'Team must contain exactly 6 Pokémon IDs'}), 400

    from nemesisGA import load_type_chart_from_db, genetic_algorithm, TYPE_CHART

    # Load type chart into memory if not already loaded
    if not TYPE_CHART:
        load_type_chart_from_db(DB_CONFIG)

    try:
        all_pokemon, opponent_team = load_all_pokemon_data(opponent_ids)
        if len(opponent_team) != 6:
            return jsonify({'error': 'Could not find all 6 opponent Pokémon in DB'}), 400

        print(f"[GA] Running for opponent team ids: {opponent_ids}")
        final_team = genetic_algorithm(
            opponent_team, all_pokemon,
            generations=30, pop_size=40
        )
        return jsonify({'nemesis_team': [p.id for p in final_team]})

    except Exception as e:
        print(f"[GA] Error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================
# ROUTE 2 — Reinforcement Learning Nemesis  →  /api/nemesis/rl
# =============================================================

# Global RL agent — loaded ONCE, reused on every request
# This is important because training takes a long time
_rl_agent = None

def get_rl_agent():
    global _rl_agent
    if _rl_agent is None:
        from nemesis import Nemesis
        print("[RL] Loading agent!")
        _rl_agent = Nemesis(db_config=DB_CONFIG)
        print("[RL] Agent ready.")
    return _rl_agent

@app.route('/api/nemesis/rl', methods=['POST'])
@login_required
def api_nemesis_rl():
    data = request.get_json()
    opponent_ids = data.get('team')
    if not opponent_ids or len(opponent_ids) != 6:
        return jsonify({'error': 'Team must contain exactly 6 Pokémon IDs'}), 400

    try:
        print(f"[RL] Getting team for opponent ids: {opponent_ids}")
        agent = get_rl_agent()
        nemesis_ids = agent.get_team(opponent_ids)
        return jsonify({'nemesis_team': nemesis_ids})

    except Exception as e:
        print(f"[RL] Error: {e}")
        return jsonify({'error': str(e)}), 500


# ---------- Auth routes ----------
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

if __name__ == '__main__':
    app.run(debug=True)
