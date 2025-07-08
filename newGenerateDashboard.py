import json
import re
from datetime import datetime, date
from collections import defaultdict
import requests
import urllib3
import argparse
import os
import time
import subprocess

# --- Configuration ---
# Suppress the InsecureRequestWarning for self-signed certificates.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==============================================================================
# SECTION 0: USER CONFIGURATION
# ==============================================================================

# --- Channel Configuration ---
# Add all channel IDs you want to scan into this list.
CHANNEL_IDS = [463, 290]

# --- Player ID to Name Mapping ---
# Edit this dictionary to map a user's numeric ID to their display name.
PLAYER_NAMES = {
    # Add your mappings here, for example:
    1: "Player 1",
    2: "Player 2",
    3: "Player 3"
}

# --- User-specific data that needs to be provided ---
# You must replace these values with the current, valid ones from your browser's developer tools.
COOKIE_STRING = "YOUR_COOKIE_STRING_HERE"
SYNO_TOKEN = "YOUR_SYNO_TOKEN_HERE"
API_URL = "https://your-server-address:port/webapi/entry.cgi"

# --- Hardcoded Script Settings ---
TIMEGUESSR_REGEX = re.compile(r"TimeGuessr #\d{3,4} \d{1,2},\d{3}/50,000")
DEFAULT_HTML_OUTPUT = 'timeguessr_dashboard.html'
GIT_REPO_FOLDER_NAME = "ToolsWebsite"  # The name of the folder containing the git repo.


# ==============================================================================
# SECTION 1: SYNOLOGY CHAT API COMMUNICATION (Multi-Channel Support)
# ==============================================================================

def get_session_headers():
    """Returns the headers required for an authenticated API call."""
    return {
        "accept": "*/*", "accept-language": "en-US",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "cookie": COOKIE_STRING, "origin": os.path.dirname(os.path.dirname(API_URL)),
        "referer": f"{os.path.dirname(os.path.dirname(API_URL))}/?launchApp=SYNO.SDS.Chat.Application",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) SynologyChat/1.2.3-232 Chrome/98.0.4758.141 Electron/17.4.7 Safari/537.36",
        "x-syno-token": SYNO_TOKEN,
    }

def fetch_message_batch(channel_id, post_id=None, prev_count=100, next_count=0):
    """Fetches a single batch of messages from the Synology Chat API for a given channel."""
    payload = {
        "api": "SYNO.Chat.Post", "method": "list", "version": "5",
        "channel_id": channel_id, "prev_count": prev_count,
        "next_count": next_count, "create_at": "null"
    }
    if post_id:
        payload["post_id"] = post_id

    try:
        response = requests.post(API_URL, headers=get_session_headers(), data=payload, verify=False)
        response.raise_for_status()
        data = response.json()
        if data.get("success"):
            return data.get("data", {}).get("posts", [])
        else:
            print(f"API Error for channel {channel_id}: {data.get('error')}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"An HTTP error occurred for channel {channel_id}: {e}")
    except ValueError:
        print(f"Failed to parse JSON response for channel {channel_id}.")
    return None

def get_local_data_filename(channel_id):
    """Generates the local data filename for a given channel."""
    return f"data_channel-{channel_id}.json"

def save_local_posts(channel_id, posts):
    """Saves a list of posts to the local data file for a given channel."""
    filename = get_local_data_filename(channel_id)
    posts_by_id = {post['post_id']: post for post in posts}
    sorted_posts = sorted(posts_by_id.values(), key=lambda p: p['create_at'])
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(sorted_posts, f, ensure_ascii=False, indent=4)
    print(f"Successfully saved {len(sorted_posts)} total messages to {filename}")

def load_local_posts(channel_id):
    """Loads posts from the local data file for a given channel."""
    filename = get_local_data_filename(channel_id)
    if not os.path.exists(filename):
        return []
    with open(filename, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode {filename}. Starting fresh for this channel.")
            return []

def download_full_channel_history(channel_id):
    """Downloads the entire message history for a given channel."""
    print(f"Starting full download for channel ID: {channel_id}...")
    all_posts = []
    
    latest_batch = fetch_message_batch(channel_id, prev_count=100, next_count=0)
    if not latest_batch:
        print(f"Failed to fetch initial batch for channel {channel_id}. Aborting.")
        return []

    all_posts.extend(latest_batch)
    oldest_post_id = latest_batch[0]['post_id']
    
    while True:
        print(f"Fetching messages before post ID: {oldest_post_id}...")
        batch = fetch_message_batch(channel_id, post_id=oldest_post_id, prev_count=100, next_count=0)
        
        if not batch:
            print("Reached the beginning of the channel history.")
            break
            
        new_oldest_id = batch[0]['post_id']
        if new_oldest_id == oldest_post_id:
            print("API returned the same oldest post ID, assuming end of history.")
            break

        all_posts.extend(batch)
        oldest_post_id = new_oldest_id
        time.sleep(0.5)

    save_local_posts(channel_id, all_posts)
    return all_posts

def update_channel_history(channel_id):
    """Updates the local channel archive with new messages for a given channel."""
    print(f"Checking for updates for channel ID: {channel_id}...")
    existing_posts = load_local_posts(channel_id)
    if not existing_posts:
        print(f"No local data found for channel {channel_id}. Running initial download instead.")
        return download_full_channel_history(channel_id)

    latest_post_id = existing_posts[-1]['post_id']
    new_posts = []
    
    while True:
        print(f"Fetching messages after post ID: {latest_post_id}...")
        batch = fetch_message_batch(channel_id, post_id=latest_post_id, prev_count=0, next_count=100)

        if not batch:
            break
        
        batch = [p for p in batch if p['post_id'] > latest_post_id]
        if not batch:
            break

        new_posts.extend(batch)
        latest_post_id = batch[-1]['post_id']
        time.sleep(0.5)

    if new_posts:
        print(f"Found {len(new_posts)} new message(s).")
        all_posts = existing_posts + new_posts
        save_local_posts(channel_id, all_posts)
        return all_posts
    else:
        print("Channel is already up-to-date.")
        return existing_posts


# ==============================================================================
# SECTION 2: TIMEGUESSR DATA PROCESSING
# ==============================================================================

def get_emoji_score(emojis):
    """Calculates the score from a string of three emojis."""
    return emojis.count('🟩') * 2 + emojis.count('🟨') * 1

def process_timeguessr_scores(posts):
    """
    Processes raw post data into structured TimeGuessr scores.
    It only takes the FIRST score a player submits on a given day.
    """
    processed_results = []
    processed_player_days = set() # Tracks (creator_id, date) to ensure uniqueness

    print("\nProcessing TimeGuessr data from all channels...")
    timeguessr_posts = [p for p in posts if 'message' in p and TIMEGUESSR_REGEX.search(p['message'])]
    print(f"Found {len(timeguessr_posts)} potential TimeGuessr posts.")
    
    for post in timeguessr_posts:
        message, post_id = post.get("message", ""), post.get("post_id")
        creator_id, create_at = post.get("creator_id"), post.get("create_at", 0)
        
        dt_object = datetime.fromtimestamp(create_at / 1000)
        game_date = dt_object.date().isoformat()
        
        if (creator_id, game_date) in processed_player_days:
            continue

        score_match = re.search(r'([\d,]+)/50,000', message)
        if not all([creator_id, score_match]): continue
        
        rounds = []
        variation_selector = '\ufe0f'
        for line in message.split('\n'):
            if not line.strip().startswith('🌎'): continue
            round_match = re.search(r'🌎(.*?)\s+📅(.*)', line.strip())
            if round_match:
                loc = round_match.group(1).strip().replace(variation_selector, '')
                date_emojis = round_match.group(2).strip().replace(variation_selector, '')
                if len(loc) == 3 and len(date_emojis) == 3:
                    rounds.append({"location_score": get_emoji_score(loc), "date_score": get_emoji_score(date_emojis)})
        
        if len(rounds) == 5:
            processed_results.append({
                "post_id": post_id, "datetime": dt_object.isoformat(),
                "creator_id": creator_id, "total_score": int(score_match.group(1).replace(',', '')),
                "rounds": rounds
            })
            processed_player_days.add((creator_id, game_date))

    print(f"Successfully processed {len(processed_results)} valid entries (first score of the day per player).")
    return processed_results


# ==============================================================================
# SECTION 3: HTML DASHBOARD GENERATION
# ==============================================================================

def get_player_name(creator_id):
    """Returns the player's name from the mapping or a default."""
    return PLAYER_NAMES.get(creator_id, f"Player {creator_id}")

def create_player_data(records):
    """Processes score records into a structured dictionary for the dashboard."""
    player_data = defaultdict(lambda: {'scores_by_date': {}, 'all_rounds': []})
    
    for record in records:
        creator_id, score = record.get('creator_id'), record.get('total_score', 0)
        game_date = record.get('datetime', '').split('T')[0]
        rounds = record.get('rounds', [])
        if not all([creator_id, game_date, rounds]): continue
        
        player_data[creator_id]['scores_by_date'][game_date] = score
        player_data[creator_id]['all_rounds'].extend(rounds)

    for pid, data in player_data.items():
        scores = list(data['scores_by_date'].values())
        if not scores: continue
        
        data['name'] = get_player_name(pid)
        data['total_games'] = len(scores)
        data['average_score'] = round(sum(scores) / data['total_games'])
        data['high_score'] = max(scores)
        data['low_score'] = min(scores) if scores else 0
        
        total_loc_score = sum(r['location_score'] for r in data['all_rounds'])
        total_date_score = sum(r['date_score'] for r in data['all_rounds'])
        num_rounds = len(data['all_rounds'])
        
        data['avg_location_score'] = round(total_loc_score / num_rounds, 2) if num_rounds > 0 else 0
        data['avg_date_score'] = round(total_date_score / num_rounds, 2) if num_rounds > 0 else 0

    return dict(player_data)

def generate_html(player_data):
    """Generates the full HTML content for the dashboard."""
    player_data_json = json.dumps(player_data, indent=4)
    
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>TimeGuessr Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script><script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; background-color: #0F172A; background-image: radial-gradient(circle at top right, #1E293B, #0F172A); }}
        .card {{ background-color: rgba(30, 41, 59, 0.5); backdrop-filter: blur(10px); border: 1px solid #334155; border-radius: 0.75rem; padding: 1.5rem; box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1); transition: all 0.3s ease-in-out; }}
        .card:hover {{ transform: translateY(-5px); box-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1); border-color: #475569; }}
        .stat-value {{ font-size: 1.875rem; font-weight: 800; color: #ffffff; }}
        .stat-label {{ font-size: 0.75rem; font-weight: 600; color: #94A3B8; text-transform: uppercase; letter-spacing: 0.05em; }}
        .gradient-text {{ background-image: linear-gradient(to right, #38BDF8, #A78BFA); -webkit-background-clip: text; background-clip: text; color: transparent; }}
    </style>
</head>
<body class="text-slate-300 antialiased">
    <div class="container mx-auto p-4 sm:p-6 lg:p-8">
        <header class="text-center mb-12">
            <h1 class="text-4xl md:text-6xl font-black text-white tracking-tighter">TimeGuessr<span class="gradient-text">Dashboard</span></h1>
            <p class="mt-4 text-lg text-slate-400 max-w-2xl mx-auto">An interactive overview of player performance and head-to-head stats.</p>
        </header>
        <section id="today-leaderboard-section" class="card mb-12 hidden"></section>
        <section id="comparison-section" class="card mb-12">
            <h2 class="text-2xl font-bold text-white mb-6 border-b border-slate-700 pb-4 flex items-center gap-3">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-blue-400"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
                Player Head-to-Head
            </h2>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
                <div><label for="player1" class="block mb-2 text-sm font-medium text-slate-400">Player 1</label><select id="player1" class="bg-slate-700 border border-slate-600 text-white text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-3 transition"><option selected disabled>Choose a player</option></select></div>
                <div><label for="player2" class="block mb-2 text-sm font-medium text-slate-400">Player 2</label><select id="player2" class="bg-slate-700 border border-slate-600 text-white text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-3 transition"><option selected disabled>Choose a player</option></select></div>
                <button id="compare-btn" class="bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-700 hover:to-purple-700 text-white font-bold py-3 px-5 rounded-lg transition duration-300 ease-in-out w-full transform hover:scale-105">Compare Players</button>
            </div>
            <div id="comparison-results" class="mt-8 hidden">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-8 mb-8"><div id="p1-stats" class="text-center"></div><div id="p2-stats" class="text-center"></div></div>
                <div class="mb-8"><h3 class="text-xl font-bold text-white mb-4">Score Over Time (Common Dates)</h3><div class="bg-slate-800/50 p-4 rounded-lg"><canvas id="scoreChart"></canvas></div></div>
                <div>
                    <h3 class="text-xl font-bold text-white mb-4">Head-to-Head Game Log</h3>
                    <div class="overflow-x-auto"><table class="min-w-full text-sm text-left text-slate-300"><thead class="text-xs text-slate-400 uppercase bg-slate-700/50"><tr><th scope="col" class="px-6 py-3">Date</th><th id="p1-table-header" scope="col" class="px-6 py-3">Player 1 Score</th><th id="p2-table-header" scope="col" class="px-6 py-3">Player 2 Score</th><th scope="col" class="px-6 py-3">Winner</th></tr></thead><tbody id="comparison-table-body"></tbody></table></div>
                </div>
            </div><div id="no-common-games-msg" class="mt-8 text-center text-yellow-400 hidden"><p>These players have no games played on the same day.</p></div>
        </section>
        <section id="overall-stats-section">
            <h2 class="text-2xl font-bold text-white mb-6 border-b border-slate-700 pb-4 flex items-center gap-3">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-purple-400"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"></path><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"></path><path d="M4 22h16"></path><path d="M10 14.66V17h4v-2.34"></path><path d="M8 17h8"></path><path d="M12 12v2.34"></path><path d="m15 9-3-3-3 3"></path></svg>
                Overall Player Leaderboard
            </h2><div id="leaderboard" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6"></div>
        </section>
    </div>
    <script>
        const playerData = {player_data_json};
        document.addEventListener('DOMContentLoaded', () => {{
            const p1Select = document.getElementById('player1'), p2Select = document.getElementById('player2'), compareBtn = document.getElementById('compare-btn');
            const leaderboard = document.getElementById('leaderboard'), results = document.getElementById('comparison-results'), noGamesMsg = document.getElementById('no-common-games-msg');
            const todayLeaderboardSection = document.getElementById('today-leaderboard-section');
            let scoreChart = null;

            function displayTodayLeaderboard() {{
                const today = new Date().toISOString().slice(0, 10);
                const todayScores = [];
                Object.entries(playerData).forEach(([pid, data]) => {{ if (data.scores_by_date[today]) {{ todayScores.push({{ name: data.name, score: data.scores_by_date[today] }}); }} }});
                if (todayScores.length > 0) {{
                    todayScores.sort((a, b) => b.score - a.score);
                    let tableHtml = `
                        <h2 class="text-2xl font-bold text-white mb-6 flex items-center gap-3">
                            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-amber-400"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 22 12 18.77 5.82 22 7 14.14 2 9.27l6.91-1.01L12 2z"></path></svg>
                            Today's Leaderboard
                        </h2>
                        <div class="overflow-x-auto"><table class="min-w-full text-sm text-left text-slate-300"><thead class="text-xs text-slate-400 uppercase bg-slate-700/50"><tr>
                        <th scope="col" class="px-6 py-3">Rank</th><th scope="col" class="px-6 py-3">Player</th><th scope="col" class="px-6 py-3">Score</th></tr></thead><tbody>`;
                    todayScores.forEach((entry, i) => {{ tableHtml += `<tr class="border-b border-slate-700 hover:bg-slate-700/50 transition">
                        <td class="px-6 py-4 font-bold text-white">${{i + 1}}</td><td class="px-6 py-4 font-medium text-white">${{entry.name}}</td>
                        <td class="px-6 py-4 font-bold text-green-400">${{entry.score.toLocaleString()}}</td></tr>`; }});
                    tableHtml += `</tbody></table></div>`;
                    todayLeaderboardSection.innerHTML = tableHtml;
                    todayLeaderboardSection.classList.remove('hidden');
                }}
            }}
            function populateSelectors() {{
                const playerIds = Object.keys(playerData).sort((a,b) => playerData[a].name.localeCompare(playerData[b].name));
                playerIds.forEach(pid => {{ [p1Select, p2Select].forEach(sel => {{ const opt = document.createElement('option'); opt.value = pid; opt.textContent = playerData[pid].name; sel.appendChild(opt); }}); }});
            }}
            function displayLeaderboard() {{
                const sorted = Object.entries(playerData).sort(([, a], [, b]) => b.average_score - a.average_score);
                leaderboard.innerHTML = '';
                sorted.forEach(([pid, stats], i) => {{
                    const card = document.createElement('div'); card.className = 'card flex flex-col justify-between';
                    card.innerHTML = `<div><div class="flex justify-between items-center mb-4"><h3 class="text-xl font-bold text-white">${{stats.name}}</h3><span class="text-sm font-bold bg-slate-700 text-slate-300 px-2 py-1 rounded">#${{i + 1}}</span></div>
                        <div class="grid grid-cols-2 gap-4 text-center">
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-green-400">${{stats.average_score.toLocaleString()}}</p><p class="stat-label">Avg Score</p></div>
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value">${{stats.total_games}}</p><p class="stat-label">Games Played</p></div>
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-blue-400">${{stats.high_score.toLocaleString()}}</p><p class="stat-label">High Score</p></div>
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-red-400">${{stats.low_score.toLocaleString()}}</p><p class="stat-label">Low Score</p></div>
                        </div><div class="grid grid-cols-2 gap-4 text-center mt-4">
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-cyan-400">${{stats.avg_location_score.toFixed(2)}}</p><p class="stat-label">Avg 🌎 Score</p></div>
                            <div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-fuchsia-400">${{stats.avg_date_score.toFixed(2)}}</p><p class="stat-label">Avg 📅 Score</p></div>
                        </div></div>`;
                    leaderboard.appendChild(card);
                }});
            }}
            function handleCompare() {{
                const p1Id = p1Select.value, p2Id = p2Select.value;
                if (!p1Id || !p2Id || p1Id === p2Id) return alert("Please select two different players.");
                const p1Scores = playerData[p1Id].scores_by_date, p2Scores = playerData[p2Id].scores_by_date;
                const commonDates = Object.keys(p1Scores).filter(date => date in p2Scores).sort();
                results.classList.add('hidden'); noGamesMsg.classList.add('hidden');
                if (commonDates.length === 0) return noGamesMsg.classList.remove('hidden');
                const p1Common = commonDates.map(d => p1Scores[d]), p2Common = commonDates.map(d => p2Scores[d]);
                updateStatCards(p1Id, p1Common, p2Id, p2Common);
                updateChart(commonDates, p1Id, p1Common, p2Id, p2Common);
                updateTable(commonDates, p1Id, p1Common, p2Id, p2Common);
                results.classList.remove('hidden');
            }}
            function createStatCardHTML(playerName, scores, wins) {{
                if (scores.length === 0) return '';
                const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length); const high = Math.max(...scores);
                return `<h3 class="text-2xl font-bold text-white mb-4">${{playerName}}</h3><div class="grid grid-cols-3 gap-4 text-center"><div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-green-400">${{wins}}</p><p class="stat-label">Wins</p></div><div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value">${{avg.toLocaleString()}}</p><p class="stat-label">Avg Score</p></div><div class="bg-slate-800/50 p-3 rounded-lg"><p class="stat-value text-blue-400">${{high.toLocaleString()}}</p><p class="stat-label">High Score</p></div></div>`;
            }}
            function updateStatCards(p1Id, p1Scores, p2Id, p2Scores) {{
                let p1Wins = p1Scores.filter((s, i) => s > p2Scores[i]).length, p2Wins = p2Scores.filter((s, i) => s > p1Scores[i]).length;
                document.getElementById('p1-stats').innerHTML = createStatCardHTML(playerData[p1Id].name, p1Scores, p1Wins);
                document.getElementById('p2-stats').innerHTML = createStatCardHTML(playerData[p2Id].name, p2Scores, p2Wins);
            }}
            function updateChart(labels, p1Id, p1Data, p2Id, p2Data) {{
                const ctx = document.getElementById('scoreChart').getContext('2d');
                if (scoreChart) scoreChart.destroy();
                scoreChart = new Chart(ctx, {{ type: 'line', data: {{ labels, datasets: [
                    {{ label: playerData[p1Id].name, data: p1Data, borderColor: '#38BDF8', backgroundColor: 'rgba(56, 189, 248, 0.2)', borderWidth: 2, tension: 0.4, fill: true, pointBackgroundColor: '#38BDF8' }},
                    {{ label: playerData[p2Id].name, data: p2Data, borderColor: '#A78BFA', backgroundColor: 'rgba(167, 139, 250, 0.2)', borderWidth: 2, tension: 0.4, fill: true, pointBackgroundColor: '#A78BFA' }}
                ]}}, options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ beginAtZero: false, ticks: {{ color: '#94A3B8' }}, grid: {{ color: '#334155' }} }}, x: {{ type: 'time', time: {{ unit: 'day' }}, ticks: {{ color: '#94A3B8' }}, grid: {{ color: '#334155' }} }} }}, plugins: {{ legend: {{ labels: {{ color: '#CBD5E1' }} }} }} }} }});
            }}
            function updateTable(dates, p1Id, p1Scores, p2Id, p2Scores) {{
                const tableBody = document.getElementById('comparison-table-body'); tableBody.innerHTML = '';
                document.getElementById('p1-table-header').textContent = `${{playerData[p1Id].name}} Score`;
                document.getElementById('p2-table-header').textContent = `${{playerData[p2Id].name}} Score`;
                dates.forEach((date, i) => {{
                    const p1s = p1Scores[i], p2s = p2Scores[i];
                    let winner, wClass;
                    if (p1s > p2s) {{ winner = playerData[p1Id].name; wClass = 'text-blue-400'; }}
                    else if (p2s > p1s) {{ winner = playerData[p2Id].name; wClass = 'text-purple-400'; }}
                    else {{ winner = 'Tie'; wClass = 'text-slate-400'; }}
                    const row = document.createElement('tr'); row.className = 'border-b border-slate-700 hover:bg-slate-700/50 transition';
                    row.innerHTML = `<td class="px-6 py-4 font-medium text-white whitespace-nowrap">${{date}}</td><td class="px-6 py-4">${{p1s.toLocaleString()}}</td><td class="px-6 py-4">${{p2s.toLocaleString()}}</td><td class="px-6 py-4 font-bold ${{wClass}}">${{winner}}</td>`;
                    tableBody.appendChild(row);
                }});
            }}
            displayTodayLeaderboard(); populateSelectors(); displayLeaderboard(); compareBtn.addEventListener('click', handleCompare);
        }});
    </script>
</body>
</html>
"""

# ==============================================================================
# SECTION 4: GIT INTEGRATION
# ==============================================================================

def run_git_command(command, cwd):
    """Runs a Git command in a specified directory and checks for errors."""
    try:
        # Using capture_output=True to hide the command's output unless there's an error.
        result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(command)}")
        print(f"Return code: {e.returncode}")
        print(f"Stderr:\n{e.stderr}")
        return False
    except FileNotFoundError:
        print("Error: 'git' command not found. Is Git installed and in your PATH?")
        return False

def commit_and_push_updates(repo_path):
    """Adds, commits, and pushes changes in the specified Git repository."""
    print(f"\nAttempting to commit and push updates for repository: {repo_path}")
    
    # Check for changes first to avoid errors on commit.
    try:
        status_check = subprocess.run(["git", "status", "--porcelain"], cwd=repo_path, check=True, capture_output=True, text=True)
        if not status_check.stdout.strip():
            print("No changes to commit. Skipping git operations.")
            return
    except (subprocess.CalledProcessError, FileNotFoundError):
         print("Could not check git status. Skipping git operations.")
         return

    print("Changes detected. Proceeding with git commands...")

    # 1. git add *
    if not run_git_command(["git", "add", "*"], cwd=repo_path):
        return

    # 2. git commit -m "<unixdatetime stamp>"
    commit_message = f"auto-update: {int(time.time())}"
    if not run_git_command(["git", "commit", "-m", commit_message], cwd=repo_path):
        return
    
    # 3. git push
    print("Pushing changes to remote repository...")
    if not run_git_command(["git", "push"], cwd=repo_path):
        return

    print(f"✅ Successfully committed and pushed updates to {repo_path}")


# ==============================================================================
# SECTION 5: MAIN EXECUTION LOGIC
# ==============================================================================

def main():
    """Main function to run the entire pipeline."""
    parser = argparse.ArgumentParser(description="AIO TimeGuessr Dashboard Generator for Synology Chat.")
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--init", action="store_true", help="Initialize and download the full channel history for all configured channels.")
    action_group.add_argument("--update", action="store_true", help="Update the channel archives with new messages for all configured channels.")
    parser.add_argument("--out", type=str, default=DEFAULT_HTML_OUTPUT, help="The full path for the output HTML file.")
    args = parser.parse_args()

    all_posts = []
    # Loop through each configured channel ID
    for channel_id in CHANNEL_IDS:
        print(f"\n{'='*20} Processing Channel ID: {channel_id} {'='*20}")
        channel_posts = []
        if args.init:
            channel_posts = download_full_channel_history(channel_id)
        elif args.update:
            channel_posts = update_channel_history(channel_id)
        all_posts.extend(channel_posts)
    
    if not all_posts:
        print("\nNo posts found or fetched across all channels. Cannot generate dashboard.")
        return

    # Sort all collected posts by creation time to ensure correct processing order
    all_posts.sort(key=lambda p: p.get('create_at', 0))

    processed_scores = process_timeguessr_scores(all_posts)
    if not processed_scores:
        print("\nNo valid TimeGuessr entries to process. Cannot generate dashboard.")
        return

    player_data = create_player_data(processed_scores)
    
    print("\nGenerating final HTML dashboard...")
    html_content = generate_html(player_data)
    
    try:
        output_dir = os.path.dirname(args.out)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            print(f"Created directory: {output_dir}")

        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"✅ Successfully generated dashboard: '{os.path.abspath(args.out)}'")

        # --- GIT PUSH LOGIC ---
        repo_dir = os.path.dirname(os.path.abspath(args.out))
        # Check if the target folder name is part of the output path
        if GIT_REPO_FOLDER_NAME in repo_dir.split(os.sep):
            commit_and_push_updates(repo_path=repo_dir)
        else:
            print(f"\nSkipping git push. Output directory '{repo_dir}' does not seem to be the correct repository.")
            print(f"(Looking for a path containing '{GIT_REPO_FOLDER_NAME}')")

    except IOError as e:
        print(f"Error writing to file '{args.out}': {e}")


if __name__ == "__main__":
    main()