// Developer Dashboard Logic

let devKey = localStorage.getItem('dev_key');
let currentPage = 1; let limit = 20;
let currentKhatmaId = null; let currentAdminUid = null;
let isLoading = false; let hasMore = true;
let selectedKhatmas = new Set();

// Selection Mode State
let isSelectionMode = false;
let selectionTargetUid = null;
let selectionHizbs = new Set();
let currentHizbData = {}; // Store raw map to check availability

if (devKey) { login(true); }

async function login(auto = false) {
    const key = auto ? devKey : document.getElementById('dev-key').value.trim();
    if (!key) return;
    try {
        const res = await fetch('/api/dev/stats', { headers: { 'X-Dev-Key': key } });
        if (res.ok) {
            devKey = key; localStorage.setItem('dev_key', key);
            document.getElementById('login-screen').classList.add('hidden');
            document.getElementById('dashboard').classList.remove('hidden');
            renderStats(await res.json()); resetAndFetch();
            window.addEventListener('scroll', handleScroll);
        } else if (!auto) { document.getElementById('error-msg').innerText = "Invalid Key"; document.getElementById('error-msg').classList.remove('hidden'); } else { logout(); }
    } catch (e) { alert("Connection failed"); }
}
function logout() { localStorage.removeItem('dev_key'); location.reload(); }
function renderStats(s) { document.getElementById('stat-khatmas').innerText = s.khatmas; document.getElementById('stat-users').innerText = s.users; document.getElementById('stat-reads').innerText = s.reads; }

function handleScroll() { if (isLoading || !hasMore) return; if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 200) { currentPage++; fetchData(true); } }
function resetAndFetch() { currentPage = 1; hasMore = true; document.getElementById('khatma-table').innerHTML = ''; document.getElementById('end-marker').style.display = 'none'; fetchData(false); }

async function fetchData(append = false) {
    if (isLoading) return; isLoading = true; document.getElementById('loading-marker').style.display = 'block';
    const url = `/api/dev/khatmas?page=${currentPage}&limit=${limit}&q=${encodeURIComponent(document.getElementById('search-input').value)}&min_progress=${document.getElementById('filter-progress').value || 0}&active_since=${document.getElementById('filter-date').value || ''}`;
    try {
        const res = await fetch(url, { headers: { 'X-Dev-Key': devKey } });
        const data = await res.json();
        const tbody = document.getElementById('khatma-table');
        if (data.khatmas.length < limit) { hasMore = false; document.getElementById('end-marker').style.display = 'block'; }
        data.khatmas.forEach(k => {
            const tr = document.createElement('tr');
            const progress = Math.round((k.current_progress / 60) * 100);
            tr.innerHTML = `
                <td><input type="checkbox" class="select-check" onchange="toggleSelect('${k.id}', this)" ${selectedKhatmas.has(k.id) ? 'checked' : ''}></td>
                <td style="font-family:monospace; color:var(--primary); cursor:pointer;" onclick="openDetails('${k.id}')">${k.id}</td>
                <td onclick="openDetails('${k.id}')" style="cursor:pointer;"><strong>${k.name}</strong></td>
                <td style="text-align:center;">${k.user_count}</td>
                <td style="color:var(--text-muted); font-size:0.85rem;">${k.updated_at ? k.updated_at.split(' ')[0] : k.created_at.split(' ')[0]}</td>
                <td><div class="progress-bar"><div class="progress-fill" style="width:${progress}%"></div></div></td>
                <td><button class="btn-sm btn-danger" onclick="deleteKhatma('${k.id}', '${k.name.replace(/'/g, "\\'")}')">🗑️</button></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) { }
    isLoading = false; document.getElementById('loading-marker').style.display = 'none';
}

// --- Batch selection ---
function toggleSelect(id, box) { if (box.checked) selectedKhatmas.add(id); else selectedKhatmas.delete(id); updateBatchBar(); }
function toggleSelectAll() {
    const all = document.querySelectorAll('#khatma-table .select-check');
    const master = document.getElementById('select-all').checked;
    all.forEach(c => { c.checked = master; if (master) selectedKhatmas.add(c.parentElement.parentElement.children[1].innerText); else selectedKhatmas.clear(); });
    updateBatchBar();
}
function updateBatchBar() {
    const bar = document.getElementById('batch-bar');
    document.getElementById('batch-count').innerText = `${selectedKhatmas.size} Selected`;
    if (selectedKhatmas.size > 0) bar.classList.remove('hidden'); else bar.classList.add('hidden');
}
function clearSelection() { selectedKhatmas.clear(); document.getElementById('select-all').checked = false; const all = document.querySelectorAll('.select-check'); all.forEach(c => c.checked = false); updateBatchBar(); }
async function batchDelete() {
    if (!confirm(`Delete ${selectedKhatmas.size} khatmas? This cannot be undone.`)) return;
    await fetch('/api/dev/khatmas/bulk_delete', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dev-Key': devKey }, body: JSON.stringify({ ids: Array.from(selectedKhatmas) }) });
    clearSelection(); resetAndFetch();
}

// --- Details ---
async function openDetails(kid) {
    currentKhatmaId = kid;
    const res = await fetch(`/api/dev/khatma/details?khatma_id=${kid}`, { headers: { 'X-Dev-Key': devKey } });
    const d = await res.json();
    currentAdminUid = d.admin.uid; // Store to update admin PIN correcty

    document.getElementById('detail-title').innerText = `${d.info.name}`;
    document.getElementById('edit-admin-pin').value = d.admin.pin;
    document.getElementById('edit-intention').value = d.info.intention;
    document.getElementById('edit-deadline').value = d.info.deadline ? d.info.deadline.split(' ')[0] : '';

    // Render Hizb Grid
    currentHizbData = d.hizb_map; // Store for validaton
    const grid = document.getElementById('hizb-grid'); grid.innerHTML = '';
    for (let i = 1; i <= 60; i++) {
        const info = d.hizb_map[String(i)]; // Keys are strings in JSON
        const div = document.createElement('div');
        div.className = `hizb-cell status-${info.status}`;
        div.id = `grid-cell-${i}`;
        div.innerText = i;
        if (info.status === 'available') {
            div.title = "Click to toggle selection (Allocating)";
            div.onclick = () => handleGridClick(i);
        } else {
            div.title = `${info.status} by ${info.user}`;
            div.onclick = () => { if (isSelectionMode) alert("This hizb is already taken."); };
            div.style.cursor = isSelectionMode ? "not-allowed" : "default";
            div.style.opacity = isSelectionMode ? "0.4" : "1";
        }
        grid.appendChild(div);
    }

    // Render Users
    const ul = document.getElementById('user-list'); ul.innerHTML = '';
    d.users.forEach(u => {
        const li = document.createElement('li');
        li.style = "padding: 10px; border-bottom: 1px solid #334155; margin-bottom: 5px; background: rgba(0,0,0,0.2); border-radius: 4px;";
        const activeBadges = u.active.map(h => `<span onclick="unassignHizb('${u.id}', ${h})" style="background:var(--warning); color:black; padding:2px 6px; border-radius:10px; font-size:0.75rem; cursor:pointer;" title="Click to Unassign">H${h} &times;</span>`).join(' ');

        li.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <span style="font-weight:600; color:white;">${u.name}</span> <span style="font-size:0.8rem; color:var(--text-muted);">(${u.completed.length} done)</span>
                    <div style="margin-top:5px; display:flex; flex-wrap:wrap; gap:5px;">${activeBadges} <button onclick="startSelectionMode('${u.id}', '${u.name.replace(/'/g, "\\'")}')" style="background:#334155; border:none; color:white; border-radius:10px; padding:2px 8px; font-size:0.75rem; width:auto;">+ Assign</button></div>
                </div>
                <div style="display:flex; flex-direction:column; gap:5px; align-items:flex-end;">
                     <div style="display:flex; gap:5px; align-items:center;">
                        <input type="text" value="${u.pin}" id="pin-${u.id}" style="width:50px; padding:2px; font-size:0.8rem; text-align:center; margin:0;">
                        <button onclick="updateUserPin('${u.id}')" class="btn-sm" style="padding:2px 5px; font-size:0.7rem; width:auto;">Save PIN</button>
                     </div>
                     <button class="btn-sm btn-danger" onclick="kickUser('${u.id}')" style="padding:2px 5px; font-size:0.7rem;">Kick User</button>
                </div>
            </div>
        `;
        ul.appendChild(li);
    });

    const modal = document.getElementById('details-modal'); modal.classList.remove('hidden'); modal.style.display = 'flex';
}
function closeModal() { document.getElementById('details-modal').style.display = 'none'; }

async function saveSetting(type) {
    const actions = { 'intention': { a: 'update_intention', v: document.getElementById('edit-intention').value }, 'deadline': { a: 'deadline', v: document.getElementById('edit-deadline').value } };
    const c = actions[type]; await fetch('/api/admin/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: c.a, hizb: c.v, khatma_id: currentKhatmaId }) }); alert("Saved");
}
async function saveAdminPin() {
    await updateUserPin(currentAdminUid, document.getElementById('edit-admin-pin').value); alert("Admin PIN Updated");
}
async function updateUserPin(uid, val) {
    const pin = val || document.getElementById(`pin-${uid}`).value;
    await fetch('/api/admin/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'update_pin', uid: uid, pin: pin, khatma_id: currentKhatmaId }) });
}

// --- Interactive Assignment ---
function startSelectionMode(uid, name) {
    isSelectionMode = true;
    selectionTargetUid = uid;
    selectionHizbs.clear();
    document.getElementById('selection-target-name').innerText = name;
    document.getElementById('selection-banner').classList.remove('hidden');

    // Visual cues
    document.getElementById('user-list').parentElement.style.opacity = '0.3'; // Dim users
    document.getElementById('user-list').parentElement.style.pointerEvents = 'none'; // Disable user list interaction

    // Reset grid visuals
    for (let i = 1; i <= 60; i++) {
        const cell = document.getElementById(`grid-cell-${i}`);
        if (cell) {
            cell.classList.remove('selected-for-assign');
            if (currentHizbData[String(i)].status !== 'available') cell.style.opacity = '0.3';
        }
    }
    updateSelectionCount();

    // Smooth scroll to top
    document.getElementById('details-modal').children[0].scrollTo({ top: 0, behavior: 'smooth' });
}

function handleGridClick(i) {
    if (!isSelectionMode) return;
    // Toggle
    if (selectionHizbs.has(i)) {
        selectionHizbs.delete(i);
        document.getElementById(`grid-cell-${i}`).classList.remove('selected-for-assign');
    } else {
        selectionHizbs.add(i);
        document.getElementById(`grid-cell-${i}`).classList.add('selected-for-assign');
    }
    updateSelectionCount();
}

function updateSelectionCount() {
    document.getElementById('selection-count').innerText = `${selectionHizbs.size} selected`;
}

function cancelSelection() {
    isSelectionMode = false;
    document.getElementById('selection-banner').classList.add('hidden');
    document.getElementById('user-list').parentElement.style.opacity = '1';
    document.getElementById('user-list').parentElement.style.pointerEvents = 'auto';
    // Restore grid opacity
    for (let i = 1; i <= 60; i++) document.getElementById(`grid-cell-${i}`).style.opacity = '1';
    openDetails(currentKhatmaId); // Redraw to clear selections
}

async function confirmSelection() {
    if (selectionHizbs.size === 0) return alert("No hizbs selected");

    const hizbs = Array.from(selectionHizbs);
    const res = await fetch('/api/admin/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'assign_bulk', uid: selectionTargetUid, hizbs: hizbs, khatma_id: currentKhatmaId }) });

    if (res.ok) {
        isSelectionMode = false;
        document.getElementById('selection-banner').classList.add('hidden');
        document.getElementById('user-list').parentElement.style.opacity = '1';
        document.getElementById('user-list').parentElement.style.pointerEvents = 'auto';
        openDetails(currentKhatmaId); // Refresh
    } else {
        alert("Failed to assign (Connection error?)");
    }
}
async function unassignHizb(uid, h) {
    if (!confirm(`Unassign Hizb ${h}?`)) return;
    const res = await fetch('/api/admin/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'unassign', uid: uid, hizb: h, khatma_id: currentKhatmaId }) });
    if (res.ok) openDetails(currentKhatmaId);
}
async function kickUser(uid) {
    if (!confirm("Kick user?")) return;
    const res = await fetch('/api/dev/khatma/remove_user', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dev-Key': devKey }, body: JSON.stringify({ uid: uid, khatma_id: currentKhatmaId }) });
    if (res.ok) openDetails(currentKhatmaId);
}
async function resetKhatma() {
    if (!confirm("⚠️ RESET this Khatma?\nThis will clear ALL current progress (unbook everyone).\nIt cannot be undone.")) return;
    const res = await fetch('/api/dev/khatma/reset', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dev-Key': devKey }, body: JSON.stringify({ khatma_id: currentKhatmaId }) });
    if (res.ok) { alert("Reset Successful"); openDetails(currentKhatmaId); resetAndFetch(); }
}
async function deleteKhatma(id, name) { if (confirm(`Delete ${name}?`)) { await fetch('/api/dev/khatma/delete', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dev-Key': devKey }, body: JSON.stringify({ khatma_id: id }) }); resetAndFetch(); } }
