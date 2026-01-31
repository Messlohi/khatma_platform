// --- Shared Utility Functions ---

// Language Toggle Helper
function toggleLang(val) {
    currentLang = val || (currentLang === 'ar' ? 'fr' : (currentLang === 'fr' ? 'en' : 'ar'));

    // Set direction based on language
    if (currentLang === 'ar') {
        document.documentElement.setAttribute('dir', 'rtl');
        document.body.dir = 'rtl';
    } else {
        document.documentElement.setAttribute('dir', 'ltr');
        document.body.dir = 'ltr';
    }

    localStorage.setItem('khatma_lang', currentLang);
    // Note: updateUI() needs to be defined in the consuming script (index.js, khatma.js)
    if (typeof updateUI === 'function') updateUI();
}

// Translation Helper
function t(key, params = {}) {
    const langObj = (typeof i18n !== 'undefined') ? i18n[currentLang] : null;
    if (!langObj) return key;

    let text = langObj[key] || key;

    // Replace placeholders like {name}
    Object.keys(params).forEach(k => {
        text = text.replace(new RegExp(`{${k}}`, 'g'), params[k]);
    });

    return text;
}

// Show/Hide Loading Overlay
function showLoading(s) {
    const el = document.getElementById('loading');
    if (el) el.style.display = s ? 'flex' : 'none';
}

// Close All Modals Helper
function closeModal() {
    document.querySelectorAll('.modal, .custom-modal-overlay').forEach(m => m.style.display = 'none');
}

// Custom Confirm Dialog (Promise-based)
function customConfirm(message, title = '') {
    return new Promise((resolve) => {
        const modal = document.getElementById('custom-confirm-modal');
        if (!modal) {
            // Fallback if modal HTML isn't present
            resolve(confirm(message));
            return;
        }

        const titleEl = document.getElementById('confirm-title');
        const messageEl = document.getElementById('confirm-message');
        const yesBtn = document.getElementById('confirm-yes-btn');
        const noBtn = document.getElementById('confirm-no-btn');

        // Note: 'i18n' and 'currentLang' must be available globally
        const t = (typeof currentLang !== 'undefined' && typeof i18n !== 'undefined') ? i18n[currentLang] : null;
        const defaultTitle = t ? (currentLang === 'ar' ? 'تأكيد' : (currentLang === 'fr' ? 'Confirmation' : 'Confirm')) : 'Confirm';

        if (titleEl) titleEl.textContent = title || defaultTitle;
        if (messageEl) messageEl.textContent = message;

        modal.style.display = 'flex';

        const handleYes = () => {
            cleanup();
            resolve(true);
        };

        const handleNo = () => {
            cleanup();
            resolve(false);
        };

        const cleanup = () => {
            modal.style.display = 'none';
            yesBtn.removeEventListener('click', handleYes);
            noBtn.removeEventListener('click', handleNo);
        };

        // Remove old listeners to prevent stacking (using clean replacements is safer, but this works if we simple add/remove)
        // Ideally we should clone the buttons to strip listeners, but cleanup() handles it for single-use.
        // Actually, the previous implementation added listeners every time without removing old ones if the promise wasn't resolved? 
        // No, the previous implementation was defining internal handlers.
        // To be safe against multiple rapid calls, we use the specific named handlers.

        yesBtn.onclick = handleYes;
        noBtn.onclick = handleNo;
    });
}

// Custom Alert Dialog (Promise-based)
function customAlert(message, title = '', icon = 'ℹ️') {
    return new Promise((resolve) => {
        const modal = document.getElementById('custom-alert-modal');
        if (!modal) {
            alert(message);
            resolve();
            return;
        }

        const iconEl = document.getElementById('alert-icon');
        const titleEl = document.getElementById('alert-title');
        const messageEl = document.getElementById('alert-message');
        const okBtn = document.getElementById('alert-ok-btn');

        const t = (typeof currentLang !== 'undefined' && typeof i18n !== 'undefined') ? i18n[currentLang] : null;
        const defaultTitle = t ? (currentLang === 'ar' ? 'تنبيه' : (currentLang === 'fr' ? 'Alerte' : 'Alert')) : 'Alert';

        if (iconEl) iconEl.textContent = icon;
        if (titleEl) titleEl.textContent = title || defaultTitle;
        if (messageEl) messageEl.textContent = message;

        modal.style.display = 'flex';

        const handleOk = () => {
            modal.style.display = 'none';
            resolve();
        };

        okBtn.onclick = handleOk;
    });
}
