"""
GTM Autonomous Agent - Production Ready
Runs 24/7 monitoring and optimizing your GTM pipeline
"""

import os
import json
import time
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from anthropic import Anthropic

# Configuration
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
HUBSPOT_TOKEN = os.environ.get('HUBSPOT_TOKEN')
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL')
RAILWAY_API_URL = os.environ.get('RAILWAY_API_URL', 'http://localhost:3000')

app = Flask(__name__)
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# Store agent actions for dashboard
agent_log = []
metrics = {
    'leads_analyzed': 0,
    'deals_monitored': 0,
    'interventions_made': 0,
    'alerts_sent': 0,
    'last_run': {}
}

def log_action(action_type, message, data=None):
    """Log all agent actions"""
    entry = {
        'timestamp': datetime.now().isoformat(),
        'type': action_type,
        'message': message,
        'data': data
    }
    agent_log.insert(0, entry)
    if len(agent_log) > 100:
        agent_log.pop()
    print(f"[{action_type}] {message}")

def send_slack(message, blocks=None):
    """Send message to Slack"""
    try:
        if not SLACK_WEBHOOK_URL:
            log_action('SLACK', 'Skipped (no webhook configured)', None)
            return
        
        payload = {'text': message}
        if blocks:
            payload['blocks'] = blocks
        
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            metrics['alerts_sent'] += 1
            log_action('SLACK', 'Message sent successfully', None)
        else:
            log_action('ERROR', f'Slack failed: {response.status_code}', None)
    except Exception as e:
        log_action('ERROR', f'Slack error: {str(e)}', None)

def get_hubspot_contacts(params=None):
    """Fetch contacts from HubSpot"""
    try:
        url = 'https://api.hubapi.com/crm/v3/objects/contacts'
        headers = {'Authorization': f'Bearer {HUBSPOT_TOKEN}'}
        response = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if response.status_code == 200:
            return response.json().get('results', [])
        return []
    except Exception as e:
        log_action('ERROR', f'HubSpot contacts fetch error: {str(e)}', None)
        return []

def get_hubspot_deals(params=None):
    """Fetch deals from HubSpot"""
    try:
        url = 'https://api.hubapi.com/crm/v3/objects/deals'
        headers = {'Authorization': f'Bearer {HUBSPOT_TOKEN}'}
        response = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if response.status_code == 200:
            return response.json().get('results', [])
        return []
    except Exception as e:
        log_action('ERROR', f'HubSpot deals fetch error: {str(e)}', None)
        return []

def create_hubspot_task(deal_id, title, notes, due_date=None):
    """Create task in HubSpot"""
    try:
        url = 'https://api.hubapi.com/crm/v3/objects/tasks'
        headers = {
            'Authorization': f'Bearer {HUBSPOT_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        task_data = {
            'properties': {
                'hs_task_subject': title,
                'hs_task_body': notes,
                'hs_task_status': 'NOT_STARTED',
                'hs_task_priority': 'HIGH',
                'hs_timestamp': due_date or datetime.now().isoformat()
            }
        }
        
        response = requests.post(url, headers=headers, json=task_data, timeout=15)
        if response.status_code == 201:
            task = response.json()
            
            # Associate with deal
            assoc_url = f"https://api.hubapi.com/crm/v3/objects/tasks/{task['id']}/associations/deals/{deal_id}/task_to_deal"
            requests.put(assoc_url, headers=headers, timeout=10)
            
            log_action('TASK', f'Created task for deal {deal_id}', {'title': title})
            metrics['interventions_made'] += 1
            return task
        return None
    except Exception as e:
        log_action('ERROR', f'Task creation error: {str(e)}', None)
        return None

def analyze_with_claude(prompt, context_data):
    """Use Claude for intelligent analysis"""
    try:
        if not anthropic_client:
            return "Claude API not configured"
        
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"{prompt}\n\nContext Data:\n{json.dumps(context_data, indent=2)}"
            }]
        )
        return message.content[0].text
    except Exception as e:
        log_action('ERROR', f'Claude analysis error: {str(e)}', None)
        return f"Analysis failed: {str(e)}"

# ===== AUTONOMOUS JOBS =====

def morning_brief_job():
    """Generate daily morning brief"""
    log_action('JOB_START', 'Morning Brief starting...', None)
    metrics['last_run']['morning_brief'] = datetime.now().isoformat()
    
    try:
        # Get leads from last 24 hours
        yesterday = (datetime.now() - timedelta(days=1)).timestamp() * 1000
        contacts = get_hubspot_contacts({
            'properties': 'firstname,lastname,email,company,lead_score_ml,territory_assignment,createdate',
            'limit': 100
        })
        
        recent_contacts = [
            c for c in contacts 
            if int(c.get('properties', {}).get('createdate', 0)) > yesterday
        ]
        
        metrics['leads_analyzed'] += len(recent_contacts)
        
        # Analyze with Claude
        analysis = analyze_with_claude(
            "You're a GTM analyst. Analyze these leads and create a prioritized morning brief for the sales team. Identify the top 3 leads to focus on and any patterns you notice.",
            {'recent_leads': recent_contacts[:10], 'total_count': len(recent_contacts)}
        )
        
        # Format Slack message
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🌅 Morning Brief - {datetime.now().strftime('%B %d, %Y')}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*New Leads:* {len(recent_contacts)}\n\n{analysis[:500]}..."}
            }
        ]
        
        send_slack(f"Morning Brief: {len(recent_contacts)} new leads", blocks)
        log_action('BRIEF', f'Morning brief sent: {len(recent_contacts)} leads analyzed', None)
        
    except Exception as e:
        log_action('ERROR', f'Morning brief failed: {str(e)}', None)

def deal_health_check_job():
    """Monitor deal health and identify risks"""
    log_action('JOB_START', 'Deal Health Check starting...', None)
    metrics['last_run']['deal_health'] = datetime.now().isoformat()
    
    try:
        deals = get_hubspot_deals({
            'properties': 'dealname,dealstage,amount,closedate,hs_lastmodifieddate',
            'limit': 100
        })
        
        metrics['deals_monitored'] += len(deals)
        
        stalled_deals = []
        seven_days_ago = (datetime.now() - timedelta(days=7)).timestamp() * 1000
        
        for deal in deals:
            props = deal.get('properties', {})
            stage = props.get('dealstage', '')
            last_modified = int(props.get('hs_lastmodifieddate', 0))
            
            # Skip closed deals
            if 'closed' in stage.lower():
                continue
                
            # Check if stalled
            if last_modified < seven_days_ago:
                stalled_deals.append({
                    'id': deal['id'],
                    'name': props.get('dealname', 'Unknown'),
                    'stage': stage,
                    'amount': props.get('amount', 0),
                    'days_stalled': (datetime.now().timestamp() * 1000 - last_modified) / (1000 * 60 * 60 * 24)
                })
        
        if stalled_deals:
            # Create tasks for stalled deals
            for deal in stalled_deals[:5]:  # Top 5 most critical
                task_title = f"⚠️ Re-engage Stalled Deal: {deal['name']}"
                task_notes = f"This deal has been inactive for {int(deal['days_stalled'])} days. Current stage: {deal['stage']}. Suggested actions:\n1. Schedule check-in call\n2. Send value reinforcement email\n3. Offer ROI analysis"
                
                create_hubspot_task(deal['id'], task_title, task_notes)
            
            # Alert team
            alert = f"🚨 *Deal Health Alert*\n\n{len(stalled_deals)} deals have stalled (no activity in 7+ days)\n\nTasks created for top {min(5, len(stalled_deals))} deals requiring attention."
            send_slack(alert)
            
            log_action('ALERT', f'Found {len(stalled_deals)} stalled deals, created {min(5, len(stalled_deals))} intervention tasks', stalled_deals[:5])
        else:
            log_action('HEALTH_CHECK', 'All deals healthy - no interventions needed', None)
            
    except Exception as e:
        log_action('ERROR', f'Deal health check failed: {str(e)}', None)

def lead_score_analysis_job():
    """Analyze lead scoring accuracy"""
    log_action('JOB_START', 'Lead Score Analysis starting...', None)
    metrics['last_run']['lead_score'] = datetime.now().isoformat()
    
    try:
        contacts = get_hubspot_contacts({
            'properties': 'lead_score_ml,hs_lead_status,createdate',
            'limit': 200
        })
        
        high_score_contacts = [
            c for c in contacts 
            if int(c.get('properties', {}).get('lead_score_ml', 0)) >= 80
        ]
        
        analysis = analyze_with_claude(
            "Analyze these high-scoring leads and identify any patterns or insights about lead quality. Are we seeing good conversion? Any recommendations?",
            {'high_score_leads': high_score_contacts[:20], 'total_analyzed': len(contacts)}
        )
        
        report = f"📊 *Lead Score Analysis*\n\n{len(high_score_contacts)} high-quality leads (score 80+) out of {len(contacts)} total\n\n{analysis[:400]}"
        send_slack(report)
        
        log_action('ANALYSIS', f'Lead score analysis complete: {len(high_score_contacts)} high-quality leads', None)
        
    except Exception as e:
        log_action('ERROR', f'Lead score analysis failed: {str(e)}', None)

# ===== WEB DASHBOARD =====

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>GTM Autonomous Agent</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            background: white;
            padding: 30px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            margin-bottom: 30px;
            text-align: center;
        }
        .header h1 { 
            font-size: 2.5em; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        .status {
            display: inline-block;
            padding: 8px 20px;
            background: #10b981;
            color: white;
            border-radius: 20px;
            font-weight: 600;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.7; }
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .metric-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        .metric-card h3 { 
            color: #667eea; 
            font-size: 0.9em; 
            text-transform: uppercase;
            margin-bottom: 10px;
        }
        .metric-value {
            font-size: 2.5em;
            font-weight: bold;
            color: #1f2937;
        }
        .log-container {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            max-height: 600px;
            overflow-y: auto;
        }
        .log-container h2 {
            color: #1f2937;
            margin-bottom: 20px;
        }
        .log-entry {
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 10px;
            border-left: 4px solid #667eea;
            background: #f9fafb;
        }
        .log-entry.error { border-left-color: #ef4444; }
        .log-entry.success { border-left-color: #10b981; }
        .log-time {
            font-size: 0.85em;
            color: #6b7280;
            margin-bottom: 5px;
        }
        .log-message { color: #1f2937; }
        .query-section {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
        }
        .query-input {
            width: 100%;
            padding: 15px;
            border: 2px solid #e5e7eb;
            border-radius: 10px;
            font-size: 1em;
            margin-bottom: 15px;
        }
        .query-button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 40px;
            border-radius: 10px;
            font-size: 1em;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s;
        }
        .query-button:hover { transform: translateY(-2px); }
        .query-result {
            margin-top: 20px;
            padding: 20px;
            background: #f9fafb;
            border-radius: 10px;
            white-space: pre-wrap;
        }
    </style>
    <script>
        function refreshData() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('leads-analyzed').textContent = data.metrics.leads_analyzed;
                    document.getElementById('deals-monitored').textContent = data.metrics.deals_monitored;
                    document.getElementById('interventions').textContent = data.metrics.interventions_made;
                    document.getElementById('alerts').textContent = data.metrics.alerts_sent;
                    
                    const logContainer = document.getElementById('log-entries');
                    logContainer.innerHTML = data.recent_logs.map(log => `
                        <div class="log-entry ${log.type.toLowerCase() === 'error' ? 'error' : 'success'}">
                            <div class="log-time">${new Date(log.timestamp).toLocaleString()}</div>
                            <div class="log-message"><strong>${log.type}:</strong> ${log.message}</div>
                        </div>
                    `).join('');
                });
        }
        
        async function askAgent() {
            const question = document.getElementById('query-input').value;
            const resultDiv = document.getElementById('query-result');
            resultDiv.textContent = 'Analyzing...';
            
            const response = await fetch('/api/query', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({question})
            });
            
            const data = await response.json();
            resultDiv.textContent = data.answer;
        }
        
        setInterval(refreshData, 5000);
        window.onload = refreshData;
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 GTM Autonomous Agent</h1>
            <div class="status">● ACTIVE & MONITORING</div>
        </div>
        
        <div class="query-section">
            <h2 style="margin-bottom: 15px;">Ask the Agent</h2>
            <input type="text" id="query-input" class="query-input" placeholder="What should I focus on today?" />
            <button class="query-button" onclick="askAgent()">Analyze</button>
            <div id="query-result" class="query-result" style="display: none;"></div>
        </div>
        
        <div class="grid">
            <div class="metric-card">
                <h3>Leads Analyzed</h3>
                <div class="metric-value" id="leads-analyzed">0</div>
            </div>
            <div class="metric-card">
                <h3>Deals Monitored</h3>
                <div class="metric-value" id="deals-monitored">0</div>
            </div>
            <div class="metric-card">
                <h3>Interventions Made</h3>
                <div class="metric-value" id="interventions">0</div>
            </div>
            <div class="metric-card">
                <h3>Alerts Sent</h3>
                <div class="metric-value" id="alerts">0</div>
            </div>
        </div>
        
        <div class="log-container">
            <h2>Agent Activity Log</h2>
            <div id="log-entries"></div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def status():
    return jsonify({
        'metrics': metrics,
        'recent_logs': agent_log[:20]
    })

@app.route('/api/query', methods=['POST'])
def query():
    question = request.json.get('question', '')
    
    # Get current pipeline data
    contacts = get_hubspot_contacts({'limit': 50})
    deals = get_hubspot_deals({'limit': 50})
    
    # Analyze with Claude
    answer = analyze_with_claude(
        f"User question: {question}\n\nProvide actionable insights and recommendations.",
        {'contacts': contacts[:10], 'deals': deals[:10]}
    )
    
    log_action('QUERY', f'Manual query: {question[:50]}...', None)
    
    # Show result
    result_div = """
    <script>
        document.getElementById('query-result').style.display = 'block';
    </script>
    """
    
    return jsonify({'answer': answer})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# ===== SCHEDULER =====

scheduler = BackgroundScheduler()

# Schedule jobs
scheduler.add_job(morning_brief_job, 'cron', hour=8, minute=0, id='morning_brief')
scheduler.add_job(deal_health_check_job, 'interval', hours=4, id='deal_health')
scheduler.add_job(lead_score_analysis_job, 'cron', hour=23, minute=0, id='lead_score')

scheduler.start()

if __name__ == '__main__':
    log_action('STARTUP', '🚀 GTM Autonomous Agent started successfully', None)
    print("\n" + "="*60)
    print("GTM AUTONOMOUS AGENT - RUNNING")
    print("="*60)
    print(f"Dashboard: http://localhost:5000")
    print(f"Health Check: http://localhost:5000/health")
    print("\nScheduled Jobs:")
    print("  • Morning Brief: Daily at 8:00 AM")
    print("  • Deal Health Check: Every 4 hours")
    print("  • Lead Score Analysis: Daily at 11:00 PM")
    print("="*60 + "\n")
    
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
