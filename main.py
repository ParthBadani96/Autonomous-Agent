"""
GTM Autonomous Agent v2 - Production Ready
Fully autonomous 24/7 monitoring and optimization
"""

import os
import json
import time
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
import requests

# Configuration
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
HUBSPOT_TOKEN = os.environ.get('HUBSPOT_TOKEN')
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL')
RAILWAY_API_URL = os.environ.get('RAILWAY_API_URL', 'http://localhost:3000')

app = Flask(__name__)

# Initialize Anthropic with proper error handling
anthropic_client = None
try:
    from anthropic import Anthropic
    if ANTHROPIC_API_KEY:
        anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        print("✓ Anthropic client initialized")
except Exception as e:
    print(f"⚠ Anthropic initialization failed: {e}")

# Agent state
agent_log = []
metrics = {
    'leads_analyzed': 0,
    'deals_monitored': 0,
    'interventions_made': 0,
    'alerts_sent': 0,
    'last_run': {},
    'uptime_start': datetime.now().isoformat()
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
    if len(agent_log) > 200:
        agent_log.pop()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{action_type}] {message}")

def send_slack(message, blocks=None):
    """Send message to Slack with retry logic"""
    try:
        if not SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL == 'demo-mode':
            log_action('SLACK', f'Demo mode: {message[:100]}', None)
            return True
        
        payload = {'text': message}
        if blocks:
            payload['blocks'] = blocks
        
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 200:
            metrics['alerts_sent'] += 1
            log_action('SLACK', 'Message sent successfully', None)
            return True
        else:
            log_action('ERROR', f'Slack failed: {response.status_code}', None)
            return False
    except Exception as e:
        log_action('ERROR', f'Slack error: {str(e)}', None)
        return False

def hubspot_request(endpoint, method='GET', data=None, params=None):
    """Unified HubSpot API request handler with error handling"""
    try:
        if not HUBSPOT_TOKEN:
            log_action('ERROR', 'HubSpot token not configured', None)
            return None
            
        url = f'https://api.hubapi.com{endpoint}'
        headers = {
            'Authorization': f'Bearer {HUBSPOT_TOKEN}',
            'Content-Type': 'application/json'
        }
        
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params or {}, timeout=15)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=15)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, json=data, timeout=15)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, timeout=15)
        else:
            return None
            
        if response.status_code in [200, 201]:
            return response.json()
        else:
            error_text = response.text[:300] if response.text else 'No error details'
            log_action('ERROR', f'HubSpot {method} {endpoint} - Status {response.status_code}: {error_text}', None)
            return None
    except Exception as e:
        log_action('ERROR', f'HubSpot request exception: {str(e)}', None)
        return None

def get_hubspot_contacts(limit=100, properties=None):
    """Fetch contacts from HubSpot with minimal properties"""
    params = {'limit': limit}
    
    # Only request standard properties
    if properties:
        params['properties'] = ','.join(properties)
    
    result = hubspot_request('/crm/v3/objects/contacts', params=params)
    return result.get('results', []) if result else []

def get_hubspot_deals(limit=100):
    """Fetch deals from HubSpot"""
    params = {
        'limit': limit,
        'properties': 'dealname,dealstage,amount,closedate,hs_lastmodifieddate,pipeline'
    }
    result = hubspot_request('/crm/v3/objects/deals', params=params)
    return result.get('results', []) if result else []

def create_hubspot_task(title, notes, due_date=None, deal_id=None):
    """Create task in HubSpot and optionally associate with deal"""
    task_data = {
        'properties': {
            'hs_task_subject': title,
            'hs_task_body': notes,
            'hs_task_status': 'NOT_STARTED',
            'hs_task_priority': 'HIGH',
            'hs_timestamp': due_date or datetime.now().isoformat()
        }
    }
    
    task = hubspot_request('/crm/v3/objects/tasks', method='POST', data=task_data)
    
    if task and deal_id:
        # Associate task with deal
        assoc_url = f'/crm/v3/objects/tasks/{task["id"]}/associations/deals/{deal_id}/task_to_deal'
        hubspot_request(assoc_url, method='PUT')
        
    if task:
        log_action('TASK', f'Created: {title}', {'task_id': task.get('id')})
        metrics['interventions_made'] += 1
        
    return task

def update_contact_score(contact_id, new_score):
    """Update lead score for a contact"""
    data = {
        'properties': {
            'lead_score_ml': new_score
        }
    }
    result = hubspot_request(f'/crm/v3/objects/contacts/{contact_id}', method='PATCH', data=data)
    if result:
        log_action('UPDATE', f'Updated contact {contact_id} score to {new_score}', None)
    return result

def analyze_with_claude(prompt, context_data):
    """Use Claude for intelligent analysis"""
    try:
        if not anthropic_client:
            return "Claude analysis unavailable - API key not configured or initialization failed."
        
        # Add current date context and conversational style
        enhanced_prompt = f"""Current date: {datetime.now().strftime('%B %d, %Y')}

You're a friendly GTM analyst having a conversation with a colleague. Be conversational, natural, and helpful - like you're chatting over coffee, not writing a formal report. Use contractions, casual language, and a warm tone. Skip the formality and bullet points unless specifically asked for lists.

{prompt}

Context Data:
{json.dumps(context_data, indent=2)}

Respond naturally as if talking to a teammate, giving practical advice in a conversational way."""
        
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{
                "role": "user",
                "content": enhanced_prompt
            }]
        )
        return message.content[0].text
    except Exception as e:
        log_action('ERROR', f'Claude analysis error: {str(e)}', None)
        return f"Analysis failed: {str(e)}"

# ===== AUTONOMOUS JOBS =====

def morning_brief_job():
    """Generate comprehensive daily morning brief"""
    log_action('JOB_START', 'Morning Brief starting...', None)
    metrics['last_run']['morning_brief'] = datetime.now().isoformat()
    
    try:
        # Get leads from last 24 hours
        yesterday_ms = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)
        contacts = get_hubspot_contacts(limit=100, properties=['firstname', 'lastname', 'email', 'company', 'createdate'])
        
        # Filter recent contacts with proper date parsing
        recent_contacts = []
        for c in contacts:
            createdate = c.get('properties', {}).get('createdate', '0')
            try:
                # Handle both timestamp and ISO format
                if isinstance(createdate, str) and 'T' in createdate:
                    # ISO format - convert to timestamp
                    dt = datetime.fromisoformat(createdate.replace('Z', '+00:00'))
                    createdate_ms = int(dt.timestamp() * 1000)
                else:
                    # Numeric timestamp
                    createdate_ms = int(createdate)
                
                if createdate_ms > yesterday_ms:
                    recent_contacts.append(c)
            except (ValueError, TypeError):
                # Skip contacts with invalid dates
                continue
        
        metrics['leads_analyzed'] += len(contacts)
        
        # Get deals data
        deals = get_hubspot_deals(limit=50)
        open_deals = [d for d in deals if 'closed' not in d.get('properties', {}).get('dealstage', '').lower()]
        
        # Calculate total pipeline value
        pipeline_value = 0
        for deal in open_deals:
            amount = deal.get('properties', {}).get('amount')
            if amount is not None:
                pipeline_value += float(amount)
        
        # Analyze with Claude
        analysis = analyze_with_claude(
            """Hey, can you take a look at the leads that came in and help me figure out what we should focus on today? I need to know which leads are worth jumping on right away and if there are any patterns I should know about. Just give me the straight talk - what's hot, what should I prioritize, and any quick action items for the team.""",
            {
                'new_leads_24h': len(recent_contacts),
                'recent_leads_sample': recent_contacts[:5],
                'total_contacts': len(contacts),
                'open_deals': len(open_deals),
                'pipeline_value': f'${pipeline_value:,.0f}'
            }
        )
        
        # Format Slack message
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Morning Brief - {datetime.now().strftime('%B %d, %Y')}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Pipeline Snapshot*\n• New Leads (24h): {len(recent_contacts)}\n• Open Deals: {len(open_deals)}\n• Pipeline Value: ${pipeline_value:,.0f}\n\n*AI Analysis:*\n{analysis[:800]}"}
            }
        ]
        
        send_slack(f"Morning Brief: {len(recent_contacts)} new leads, ${pipeline_value:,.0f} pipeline", blocks)
        log_action('BRIEF', f'Morning brief completed: {len(recent_contacts)} leads, {len(open_deals)} open deals', None)
        
    except Exception as e:
        log_action('ERROR', f'Morning brief failed: {str(e)}', None)

def deal_health_check_job():
    """Monitor deal health and create interventions"""
    log_action('JOB_START', 'Deal Health Check starting...', None)
    metrics['last_run']['deal_health'] = datetime.now().isoformat()
    
    try:
        deals = get_hubspot_deals(limit=100)
        metrics['deals_monitored'] += len(deals)
        
        stalled_deals = []
        seven_days_ago_ms = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        
        for deal in deals:
            props = deal.get('properties', {})
            stage = props.get('dealstage', '')
            last_modified = props.get('hs_lastmodifieddate', '0')
            
            # Parse last modified date
            try:
                if isinstance(last_modified, str) and 'T' in last_modified:
                    dt = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
                    last_modified_ms = int(dt.timestamp() * 1000)
                else:
                    last_modified_ms = int(last_modified)
            except (ValueError, TypeError):
                continue
            
            if 'closed' not in stage.lower() and last_modified_ms < seven_days_ago_ms:
                days_stalled = (datetime.now().timestamp() * 1000 - last_modified_ms) / (1000 * 60 * 60 * 24)
                amount = props.get('amount')
                deal_amount = float(amount) if amount is not None else 0
                
                stalled_deals.append({
                    'id': deal['id'],
                    'name': props.get('dealname', 'Unknown'),
                    'stage': stage,
                    'amount': deal_amount,
                    'days_stalled': int(days_stalled)
                })
        
        if stalled_deals:
            # Sort by amount (highest value first)
            stalled_deals.sort(key=lambda x: x['amount'], reverse=True)
            
            # Create tasks for top 5 stalled deals
            tasks_created = 0
            for deal in stalled_deals[:5]:
                task_title = f"URGENT: Re-engage {deal['name']}"
                task_notes = f"""This ${deal['amount']:,.0f} deal has been stalled for {deal['days_stalled']} days.

Current Stage: {deal['stage']}

Recommended Actions:
1. Call the contact immediately
2. Send a value reinforcement email with ROI data
3. Offer a limited-time incentive or discount
4. Schedule a decision-maker meeting
5. Address any blocking concerns

DO NOT let this deal go cold. Take action today."""
                
                due_date = (datetime.now() + timedelta(hours=4)).isoformat()
                task = create_hubspot_task(task_title, task_notes, due_date, deal['id'])
                if task:
                    tasks_created += 1
            
            # Send Slack alert
            alert_text = f"Deal Health Alert: {len(stalled_deals)} stalled deals found"
            alert_blocks = [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": "Deal Health Alert"}
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{len(stalled_deals)} deals* have stalled (7+ days no activity)\n\nCreated {tasks_created} intervention tasks for top deals:\n" + "\n".join([f"• {d['name']}: ${d['amount']:,.0f} ({d['days_stalled']} days)" for d in stalled_deals[:5]])}
                }
            ]
            
            send_slack(alert_text, alert_blocks)
            log_action('ALERT', f'Deal health: {len(stalled_deals)} stalled, {tasks_created} interventions created', stalled_deals[:5])
        else:
            log_action('HEALTH_CHECK', 'All deals healthy - no interventions needed', None)
            
    except Exception as e:
        log_action('ERROR', f'Deal health check failed: {str(e)}', None)

def lead_score_optimizer_job():
    """Analyze and optimize lead scoring"""
    log_action('JOB_START', 'Lead Score Optimizer starting...', None)
    metrics['last_run']['lead_score'] = datetime.now().isoformat()
    
    try:
                
        contacts = get_hubspot_contacts(limit=200, properties=['firstname', 'lastname', 'email', 'company', 'createdate'])
        deals = get_hubspot_deals(limit=100)
        
        # Analyze high-scoring leads
        high_score_leads = [c for c in contacts if int(c.get('properties', {}).get('lead_score_ml', 0)) >= 80]
        
        # Get closed won deals to analyze conversion patterns
        closed_won = [d for d in deals if 'closedwon' in d.get('properties', {}).get('dealstage', '').lower()]
        
        analysis = analyze_with_claude(
            """So I'm looking at our lead scoring and trying to figure out if it's actually working or if we need to tweak it. Can you check if the high-scoring leads are actually converting? Are we missing anything? Just let me know what you think and if there's anything we should adjust.""",
            {
                'high_score_leads': len(high_score_leads),
                'high_score_sample': high_score_leads[:10],
                'closed_won_count': len(closed_won),
                'total_analyzed': len(contacts)
            }
        )
        
        # Send report to Slack
        report_blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Lead Score Analysis"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Scoring Performance*\n• High-quality leads (80+): {len(high_score_leads)}\n• Recent conversions: {len(closed_won)}\n• Total analyzed: {len(contacts)}\n\n*AI Insights:*\n{analysis[:700]}"}
            }
        ]
        
        send_slack("Lead Score Analysis Complete", report_blocks)
        log_action('ANALYSIS', f'Lead scoring analyzed: {len(high_score_leads)} high-quality leads', None)
        
    except Exception as e:
        log_action('ERROR', f'Lead score analysis failed: {str(e)}', None)

def generate_weekly_report():
    """Generate comprehensive weekly report"""
    log_action('JOB_START', 'Weekly Report generation starting...', None)
    
    try:
        # Get data from last 7 days
        week_ago_ms = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        
        contacts = get_hubspot_contacts(limit=200)
        deals = get_hubspot_deals(limit=100)
        
        # Filter weekly leads with proper date parsing
        weekly_leads = []
        for c in contacts:
            createdate = c.get('properties', {}).get('createdate', '0')
            try:
                if isinstance(createdate, str) and 'T' in createdate:
                    dt = datetime.fromisoformat(createdate.replace('Z', '+00:00'))
                    createdate_ms = int(dt.timestamp() * 1000)
                else:
                    createdate_ms = int(createdate)
                
                if createdate_ms > week_ago_ms:
                    weekly_leads.append(c)
            except (ValueError, TypeError):
                continue
        weekly_closed = [d for d in deals if 'closed' in d.get('properties', {}).get('dealstage', '').lower()]
        
        revenue = sum([float(d.get('properties', {}).get('amount', 0)) for d in weekly_closed if 'won' in d.get('properties', {}).get('dealstage', '').lower()])
        
        report = analyze_with_claude(
            """Can you put together a quick summary of how this week went? I need to understand what's working, what's not, and what we should focus on next week. Just talk to me like we're reviewing the week over coffee - what jumped out at you?""",
            {
                'weekly_leads': len(weekly_leads),
                'weekly_closed_deals': len(weekly_closed),
                'weekly_revenue': f'${revenue:,.0f}',
                'lead_samples': weekly_leads[:10],
                'deal_samples': weekly_closed[:5]
            }
        )
        
        # Send to Slack
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Weekly GTM Report - Week of {(datetime.now() - timedelta(days=7)).strftime('%b %d')}"}
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Weekly Metrics*\n• New Leads: {len(weekly_leads)}\n• Closed Deals: {len(weekly_closed)}\n• Revenue: ${revenue:,.0f}\n\n{report[:900]}"}
            }
        ]
        
        send_slack("Weekly GTM Report", blocks)
        log_action('REPORT', f'Weekly report generated: {len(weekly_leads)} leads, ${revenue:,.0f} revenue', None)
        
    except Exception as e:
        log_action('ERROR', f'Weekly report failed: {str(e)}', None)

# ===== WEB DASHBOARD =====

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>GTM Autonomous Agent v2</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
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
            margin-right: 10px;
        }
        .query-button:hover:not(:disabled) { transform: translateY(-2px); }
        .query-button:disabled { 
            opacity: 0.6; 
            cursor: not-allowed;
        }
        .query-result {
            margin-top: 20px;
            padding: 20px;
            background: #f9fafb;
            border-radius: 10px;
            white-space: pre-wrap;
            display: none;
            line-height: 1.6;
        }
        .query-result.show {
            display: block;
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
        .log-entry.warning { border-left-color: #f59e0b; }
        .log-time {
            font-size: 0.85em;
            color: #6b7280;
            margin-bottom: 5px;
        }
        .log-message { color: #1f2937; }
        .action-buttons {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .action-btn {
            padding: 10px 20px;
            background: #10b981;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
        }
        .action-btn:hover { background: #059669; }
    </style>
    <script>
        async function refreshData() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                document.getElementById('leads-analyzed').textContent = data.metrics.leads_analyzed;
                document.getElementById('deals-monitored').textContent = data.metrics.deals_monitored;
                document.getElementById('interventions').textContent = data.metrics.interventions_made;
                document.getElementById('alerts').textContent = data.metrics.alerts_sent;
                
                const logContainer = document.getElementById('log-entries');
                logContainer.innerHTML = data.recent_logs.map(log => {
                    let cssClass = 'success';
                    if (log.type.toLowerCase().includes('error')) cssClass = 'error';
                    if (log.type.toLowerCase().includes('alert')) cssClass = 'warning';
                    
                    return `
                        <div class="log-entry ${cssClass}">
                            <div class="log-time">${new Date(log.timestamp).toLocaleString()}</div>
                            <div class="log-message"><strong>${log.type}:</strong> ${log.message}</div>
                        </div>
                    `;
                }).join('');
            } catch (error) {
                console.error('Refresh failed:', error);
            }
        }
        
        async function askAgent() {
            const question = document.getElementById('query-input').value;
            const resultDiv = document.getElementById('query-result');
            const button = document.querySelector('.query-button');
            
            if (!question.trim()) {
                alert('Please enter a question');
                return;
            }
            
            resultDiv.style.display = 'block';
            resultDiv.classList.add('show');
            resultDiv.textContent = 'Analyzing your pipeline... This may take 10-15 seconds.';
            button.disabled = true;
            button.textContent = 'Analyzing...';
            
            try {
                const response = await fetch('/api/query', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({question})
                });
                
                const data = await response.json();
                resultDiv.textContent = data.answer || 'No response received';
            } catch (error) {
                resultDiv.textContent = 'Error: ' + error.message;
            } finally {
                button.disabled = false;
                button.textContent = 'Analyze';
            }
        }
        
        async function triggerJob(jobName) {
            try {
                const response = await fetch(`/api/trigger/${jobName}`, {method: 'POST'});
                const data = await response.json();
                alert(data.message);
                refreshData();
            } catch (error) {
                alert('Failed to trigger job: ' + error.message);
            }
        }
        
        async function generateReport() {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Generating...';
            
            try {
                const response = await fetch('/api/report/weekly');
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `gtm-report-${new Date().toISOString().split('T')[0]}.txt`;
                a.click();
                alert('Report downloaded!');
            } catch (error) {
                alert('Report generation failed: ' + error.message);
            } finally {
                btn.disabled = false;
                btn.textContent = 'Generate Weekly Report';
            }
        }
        
        setInterval(refreshData, 5000);
        window.onload = refreshData;
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>GTM Autonomous Agent v2</h1>
            <div class="status">ACTIVE & MONITORING</div>
        </div>
        
        <div class="query-section">
            <h2 style="margin-bottom: 15px;">Ask the Agent</h2>
            <input type="text" id="query-input" class="query-input" placeholder="What should I focus on today? Which deals need attention?" />
            <button class="query-button" onclick="askAgent()">Analyze</button>
            <button class="action-btn" onclick="generateReport()">Generate Weekly Report</button>
            <div id="query-result" class="query-result"></div>
            
            <div class="action-buttons" style="margin-top: 20px;">
                <button class="action-btn" onclick="triggerJob('morning_brief')">Run Morning Brief</button>
                <button class="action-btn" onclick="triggerJob('deal_health')">Check Deal Health</button>
                <button class="action-btn" onclick="triggerJob('lead_score')">Analyze Lead Scores</button>
            </div>
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
        'recent_logs': agent_log[:30],
        'status': 'healthy'
    })

@app.route('/api/query', methods=['POST'])
def query():
    """Handle manual queries from dashboard"""
    try:
        question = request.json.get('question', '')
        
        if not question:
            return jsonify({'answer': 'Please provide a question'})
        
        log_action('QUERY', f'Manual query: {question[:50]}...', None)
        
        # Get current pipeline data with error handling
        contacts = []
        deals = []
        
        try:
            contacts = get_hubspot_contacts(limit=100, properties=['firstname', 'lastname', 'email', 'company', 'createdate'])
        except Exception as e:
            log_action('ERROR', f'Failed to fetch contacts: {str(e)}', None)
        
        try:
            deals = get_hubspot_deals(limit=100)
        except Exception as e:
            log_action('ERROR', f'Failed to fetch deals: {str(e)}', None)
        
        # Calculate key metrics
        pipeline_value = 0
        high_score_leads = []
        
        try:
            open_deals = [d for d in deals if 'closed' not in d.get('properties', {}).get('dealstage', '').lower()]
            for deal in open_deals:
                amount = deal.get('properties', {}).get('amount')
                if amount is not None:
                    pipeline_value += float(amount)
        except Exception as e:
            log_action('ERROR', f'Pipeline calculation error: {str(e)}', None)
        
        # Analyze with Claude
        answer = analyze_with_claude(
            f"""Hey, someone from the team just asked me: "{question}"
            
Can you help me answer this based on what's happening in our pipeline right now? Just talk to me like we're colleagues figuring this out together - give me the real talk on what's going on and what they should do about it.""",
            {
                'total_contacts': len(contacts),
                'total_deals': len(deals),
                'pipeline_value': f'${pipeline_value:,.0f}',
                'contact_samples': contacts[:15] if contacts else [],
                'deal_samples': deals[:15] if deals else []
            }
        )
        
        return jsonify({'answer': answer})
        
    except Exception as e:
        error_msg = f"Query failed: {str(e)}"
        log_action('ERROR', error_msg, None)
        return jsonify({'answer': f"I ran into an issue: {error_msg}\n\nPlease check the Railway logs for details."})


@app.route('/api/trigger/<job_name>', methods=['POST'])
def trigger_job(job_name):
    """Manually trigger scheduled jobs"""
    jobs = {
        'morning_brief': morning_brief_job,
        'deal_health': deal_health_check_job,
        'lead_score': lead_score_optimizer_job,
        'weekly_report': generate_weekly_report
    }
    
    if job_name not in jobs:
        return jsonify({'error': 'Invalid job name'}), 400
    
    try:
        # Run job in background
        jobs[job_name]()
        return jsonify({'message': f'{job_name} job triggered successfully'})
    except Exception as e:
        log_action('ERROR', f'Manual trigger failed for {job_name}: {str(e)}', None)
        return jsonify({'error': str(e)}), 500

@app.route('/api/report/weekly')
def weekly_report():
    """Generate downloadable weekly report"""
    try:
        week_ago_ms = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)
        
        contacts = get_hubspot_contacts(limit=300, properties=['firstname', 'lastname', 'email', 'company', 'createdate'])
        deals = get_hubspot_deals(limit=150)
        
        # Filter weekly leads with proper date parsing
        weekly_leads = []
        for c in contacts:
            createdate = c.get('properties', {}).get('createdate', '0')
            try:
                if isinstance(createdate, str) and 'T' in createdate:
                    dt = datetime.fromisoformat(createdate.replace('Z', '+00:00'))
                    createdate_ms = int(dt.timestamp() * 1000)
                else:
                    createdate_ms = int(createdate)
                
                if createdate_ms > week_ago_ms:
                    weekly_leads.append(c)
            except (ValueError, TypeError):
                continue
        weekly_closed = [d for d in deals if 'closed' in d.get('properties', {}).get('dealstage', '').lower()]
        
        won_deals = [d for d in weekly_closed if 'won' in d.get('properties', {}).get('dealstage', '').lower()]
        revenue = 0
        for deal in won_deals:
            amount = deal.get('properties', {}).get('amount')
            if amount is not None:
                revenue += float(amount)
        
        report_content = f"""
GTM WEEKLY REPORT
Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
Report Period: Last 7 Days

════════════════════════════════════════════════════════════════

EXECUTIVE SUMMARY
════════════════════════════════════════════════════════════════

New Leads: {len(weekly_leads)}
Closed Deals: {len(weekly_closed)}
Won Deals: {len(won_deals)}
Weekly Revenue: ${revenue:,.2f}

Conversion Rate: {(len(won_deals) / len(weekly_leads) * 100) if weekly_leads else 0:.1f}%
Average Deal Size: ${(revenue / len(won_deals)) if won_deals else 0:,.2f}

════════════════════════════════════════════════════════════════

TOP PERFORMING LEADS (This Week)
════════════════════════════════════════════════════════════════

"""
        
        # Add top leads
        top_leads = sorted(weekly_leads, key=lambda x: int(x.get('properties', {}).get('lead_score_ml', 0)), reverse=True)[:10]
        for i, lead in enumerate(top_leads, 1):
            props = lead.get('properties', {})
            report_content += f"{i}. {props.get('firstname', '')} {props.get('lastname', '')} - {props.get('company', 'Unknown')}\n"
            report_content += f"   Score: {props.get('lead_score_ml', 'N/A')} | Territory: {props.get('territory_assignment', 'N/A')}\n\n"
        
        report_content += f"""
════════════════════════════════════════════════════════════════

CLOSED DEALS (This Week)
════════════════════════════════════════════════════════════════

"""
        
        # Add closed deals
        for i, deal in enumerate(weekly_closed[:10], 1):
            props = deal.get('properties', {})
            report_content += f"{i}. {props.get('dealname', 'Unknown Deal')}\n"
            report_content += f"   Amount: ${float(props.get('amount', 0)):,.2f} | Stage: {props.get('dealstage', 'Unknown')}\n\n"
        
        # Get AI analysis
        ai_insights = analyze_with_claude(
            """Alright, I'm putting together the weekly report and need your take on how things went. What stood out to you? Any red flags I should mention? What should we double down on next week? Just give me your honest thoughts like we're talking shop.""",
            {
                'weekly_summary': {
                    'leads': len(weekly_leads),
                    'deals': len(weekly_closed),
                    'revenue': revenue,
                    'conversion_rate': f"{(len(won_deals) / len(weekly_leads) * 100) if weekly_leads else 0:.1f}%"
                }
            }
        )
        
        report_content += f"""
════════════════════════════════════════════════════════════════

AI STRATEGIC INSIGHTS
════════════════════════════════════════════════════════════════

{ai_insights}

════════════════════════════════════════════════════════════════

Report generated by GTM Autonomous Agent v2
For questions or custom reports, contact your RevOps team
"""
        
        log_action('REPORT', 'Weekly report generated', None)
        
        return Response(
            report_content,
            mimetype='text/plain',
            headers={'Content-Disposition': f'attachment; filename=gtm-report-{datetime.now().strftime("%Y-%m-%d")}.txt'}
        )
        
    except Exception as e:
        log_action('ERROR', f'Report generation failed: {str(e)}', None)
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint"""
    uptime_seconds = (datetime.now() - datetime.fromisoformat(metrics['uptime_start'])).total_seconds()
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'uptime_seconds': uptime_seconds,
        'anthropic_configured': anthropic_client is not None,
        'hubspot_configured': bool(HUBSPOT_TOKEN),
        'slack_configured': bool(SLACK_WEBHOOK_URL)
    })

# ===== SCHEDULER SETUP =====

def start_scheduler():
    """Initialize and start the job scheduler"""
    scheduler = BackgroundScheduler()
    
    # Morning Brief - 8:00 AM daily
    scheduler.add_job(
        morning_brief_job, 
        'cron', 
        hour=8, 
        minute=0, 
        id='morning_brief',
        replace_existing=True
    )
    
    # Deal Health Check - Every 4 hours
    scheduler.add_job(
        deal_health_check_job, 
        'interval', 
        hours=4, 
        id='deal_health',
        replace_existing=True
    )
    
    # Lead Score Optimizer - 11:00 PM daily
    scheduler.add_job(
        lead_score_optimizer_job, 
        'cron', 
        hour=23, 
        minute=0, 
        id='lead_score',
        replace_existing=True
    )
    
    # Weekly Report - Monday 9:00 AM
    scheduler.add_job(
        generate_weekly_report,
        'cron',
        day_of_week='mon',
        hour=9,
        minute=0,
        id='weekly_report',
        replace_existing=True
    )
    
    scheduler.start()
    log_action('STARTUP', 'Scheduler started - all jobs configured', None)
    
    return scheduler

# ===== APPLICATION STARTUP =====

if __name__ == '__main__':
    try:
        print("\n" + "="*70)
        print("GTM AUTONOMOUS AGENT v2 - PRODUCTION")
        print("="*70)
        print(f"Dashboard: http://localhost:5000")
        print(f"Health Check: http://localhost:5000/health")
        print("\nScheduled Jobs:")
        print("  • Morning Brief: Daily at 8:00 AM")
        print("  • Deal Health Check: Every 4 hours")
        print("  • Lead Score Optimizer: Daily at 11:00 PM")
        print("  • Weekly Report: Monday at 9:00 AM")
        print("\nConfiguration:")
        print(f"  • Anthropic: {'✓ Configured' if anthropic_client else '✗ Not configured'}")
        print(f"  • HubSpot: {'✓ Configured' if HUBSPOT_TOKEN else '✗ Not configured'}")
        print(f"  • Slack: {'✓ Configured' if SLACK_WEBHOOK_URL else '✗ Not configured'}")
        print("="*70 + "\n")
        
        log_action('STARTUP', 'GTM Autonomous Agent v2 started successfully', None)
        
        # Start the scheduler
        scheduler = start_scheduler()
        
        # Run Flask app
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
        
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
