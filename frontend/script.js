// script.js - AegisMesh Frontend Logic
const GATEWAY_URL = 'http://127.0.0.1:9000';

const DOM = {
    input: document.getElementById('query-input'),
    btnSend: document.getElementById('send-btn'),
    chat: document.getElementById('chat-history'),
    confMeter: document.getElementById('confidence-meter'),
    confText: document.getElementById('confidence-text'),
    confStatus: document.getElementById('confidence-status'),
    statTrace: document.getElementById('stat-trace'),
    statLatency: document.getElementById('stat-latency'),
    statAgents: document.getElementById('stat-agents'),
    statFacts: document.getElementById('stat-facts'),
    statReason: document.getElementById('stat-reason'),
    logContainer: document.getElementById('log-container'),
    backendStatus: document.getElementById('backend-status')
};

// Auto-resize textarea
DOM.input.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
});

// Check Gateway Status
async function pingGateway() {
    try {
        const res = await fetch(`${GATEWAY_URL}/health`);
        if (res.ok) {
            DOM.backendStatus.innerHTML = `<span class="status-dot healthy"></span> Online: 127.0.0.1:9000`;
            return true;
        }
    } catch (e) {
        DOM.backendStatus.innerHTML = `<span class="status-dot healthy" style="background:#F87171;box-shadow:0 0 8px #F87171"></span> Offline / Booting...`;
        return false;
    }
}
setInterval(pingGateway, 5000);
pingGateway();

// Basic Markdown parser for report block
function formatReportText(text) {
    if (!text) return 'No report generated.';
    let html = text
        .replace(/\*\*(.*?)\*\*/g, '<strong class="md-strong">$1</strong>')
        .replace(/### (.*?)$/gm, '<h3 class="md-h3">$1</h3>')
        .replace(/- (.*?)$/gm, '<li class="md-list">$1</li>')
        .replace(/\n\n/g, '<br><br>');
    return html;
}

// Write to Live Log side panel
function streamLog(msg, type = '') {
    const div = document.createElement('div');
    div.className = `log-entry ${type}`;
    div.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    DOM.logContainer.appendChild(div);
    DOM.logContainer.scrollTop = DOM.logContainer.scrollHeight;
}

function updateConfidenceMeter(score) {
    // Score is 0.0 to 1.0. SVG circle dasharray is 283.
    const pct = score * 100;
    const offset = 283 - ((283 * pct) / 100);
    DOM.confMeter.style.strokeDashoffset = offset;
    DOM.confText.textContent = `${Math.round(pct)}%`;
    
    // Status text logic based on reasoning tiers
    if (pct === 0) DOM.confStatus.textContent = 'AWAITING EVIDENCE';
    else if (pct >= 90) {
        DOM.confStatus.textContent = 'DETERMINISTIC BYPASS';
        DOM.confStatus.style.color = '#34D399';
        DOM.confMeter.style.stroke = '#34D399';
    } else if (pct >= 70) {
        DOM.confStatus.textContent = 'HIGH CONFIDENCE';
        DOM.confStatus.style.color = '#38BDF8';
        DOM.confMeter.style.stroke = '#38BDF8';
    } else {
        DOM.confStatus.style.color = '#F87171';
        DOM.confMeter.style.stroke = '#F87171';
        DOM.confStatus.textContent = 'LLM FALLBACK ENGAGED';
    }
}

function appendMessage(sender, content, isRawHtml = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `message ${sender}`;
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = sender === 'system' ? '▲' : 'U';
    
    const body = document.createElement('div');
    body.className = 'content';
    if (isRawHtml) body.innerHTML = content;
    else {
        const p = document.createElement('p');
        p.textContent = content;
        body.appendChild(p);
    }
    
    wrapper.appendChild(avatar);
    wrapper.appendChild(body);
    DOM.chat.appendChild(wrapper);
    DOM.chat.scrollTop = DOM.chat.scrollHeight;
}

async function handleInvestigation() {
    const query = DOM.input.value.trim();
    if (!query) return;

    // Reset UI
    DOM.input.value = '';
    DOM.input.style.height = 'auto';
    DOM.btnSend.disabled = true;
    DOM.input.disabled = true;
    updateConfidenceMeter(0);
    
    // Reset Stats
    DOM.statTrace.textContent = '--';
    DOM.statLatency.textContent = '--s';
    DOM.statAgents.textContent = '--';
    DOM.statFacts.textContent = '--';
    DOM.statReason.textContent = '--';

    appendMessage('user', query);
    streamLog('Query captured.', 'process');
    streamLog('Dispatching to Supervisor API...', 'process');

    try {
        const response = await fetch(`${GATEWAY_URL}/investigate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        if (response.status === 504) {
            throw new Error('Investigation timed out.');
        } else if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail?.error || 'Internal Server Error');
        }

        const data = await response.json();
        
        // Update Side Panel
        DOM.statTrace.textContent = data.trace_id.split('_')[1] || data.trace_id;
        DOM.statLatency.textContent = `${data.duration_seconds.toFixed(2)}s`;
        DOM.statAgents.textContent = data.agents_dispatched;
        DOM.statFacts.textContent = data.facts_collected;
        DOM.statReason.textContent = data.termination_reason;
        
        updateConfidenceMeter(data.confidence_score || 0);

        streamLog(`Supervisor cycle complete in ${data.duration_seconds.toFixed(1)}s`, 'success');
        if (data.low_confidence) streamLog('Warning: Target metrics inconclusive.', 'error');

        // Render Report
        const reportHtml = formatReportText(data.report);
        appendMessage('system', reportHtml, true);

    } catch (e) {
        streamLog(`Exception caught: ${e.message}`, 'error');
        appendMessage('system', `[!] Graph Interrupted: ${e.message}`);
    } finally {
        DOM.btnSend.disabled = false;
        DOM.input.disabled = false;
        DOM.input.focus();
    }
}

// Events
DOM.btnSend.addEventListener('click', handleInvestigation);
DOM.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleInvestigation();
    }
});
