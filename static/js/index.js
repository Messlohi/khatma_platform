// Core Logic using Shared Assets

let currentLang = 'ar';
let createdKhatmaId = '';

// Check for saved language preference or default to Arabic
if (localStorage.getItem('khatma_lang')) {
    currentLang = localStorage.getItem('khatma_lang');
    // We need to wait for DOMContentLoaded to setLang effectively if script runs in head?
    // Script is at end of body in index.html, so it's fine.
}

document.addEventListener('DOMContentLoaded', () => {
    setLang(currentLang);
});

function setLang(lang) {
    if (typeof toggleLang === 'function') {
        // Use shared utils if available, but here we set specific lang
        currentLang = lang;
        localStorage.setItem('khatma_lang', lang);
        document.documentElement.lang = lang;
        document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
        document.body.dir = lang === 'ar' ? 'rtl' : 'ltr';

        document.querySelectorAll('.lang-btn').forEach(btn => btn.classList.remove('active'));
        // Find button by onclick content or add IDs? Using existing structure:
        const btns = document.querySelectorAll('.lang-btn');
        if (lang === 'ar' && btns[0]) btns[0].classList.add('active');
        if (lang === 'en' && btns[1]) btns[1].classList.add('active');
        if (lang === 'fr' && btns[2]) btns[2].classList.add('active');

        // Verify i18n exists
        if (typeof i18n === 'undefined') return console.error('i18n not loaded');

        document.querySelectorAll('[data-t]').forEach(el => {
            const key = el.getAttribute('data-t');
            if (i18n[lang][key]) {
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    el.placeholder = i18n[lang][key];
                } else if (key === 'admin_login_id_hint' || key === 'header_subtitle') {
                    el.innerHTML = i18n[lang][key];
                } else {
                    el.textContent = i18n[lang][key];
                }
            }
        });

        document.title = i18n[lang].header_title || 'Khatma System';
    } else {
        // Fallback if utils not loaded (should not happen)
        console.error("Utils not loaded");
    }
}

function showWelcome() {
    document.getElementById('welcome-screen').classList.remove('hidden');
    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('success-screen').classList.add('hidden');
    document.getElementById('loading').classList.add('hidden');
}

function showCreateForm() {
    document.getElementById('welcome-screen').classList.add('hidden');
    document.getElementById('create-form').classList.remove('hidden');
}

async function createKhatma(event) {
    event.preventDefault();

    const khatmaName = document.getElementById('khatma-name').value;

    // Optional Admin Logic
    const enableAdmin = document.getElementById('enable-admin').checked;
    const adminName = enableAdmin ? document.getElementById('admin-name').value : null;
    const adminPin = enableAdmin ? document.getElementById('admin-pin').value : null;

    if (enableAdmin && (!adminName || !adminPin)) {
        return customAlert(t('alert_error_create'), '⚠️');
    }

    const intention = document.getElementById('intention').value;
    const deadline = document.getElementById('deadline').value;

    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('loading').classList.remove('hidden');

    try {
        const response = await fetch('/api/khatma/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: khatmaName,
                admin_name: adminName,
                admin_pin: adminPin,
                intention: intention || '',
                deadline: deadline || null
            })
        });

        const data = await response.json();

        if (data.success) {
            createdKhatmaId = data.khatma_id;
            const link = `${window.location.origin}/${createdKhatmaId}`;
            document.getElementById('khatma-link').textContent = link;

            // IMPORTANT: Store admin credentials for automatic login ONLY if admin was created
            if (data.admin_uid) {
                localStorage.setItem('web_user_name', adminName);
                localStorage.setItem('web_user_pin', adminPin);
                localStorage.setItem('web_user_uid', data.admin_uid);
                localStorage.setItem('is_admin', 'true');
                localStorage.setItem('admin_khatma_id', createdKhatmaId);
            } else {
                // Clear any previous admin session
                localStorage.removeItem('is_admin');
                localStorage.removeItem('admin_khatma_id');
                localStorage.removeItem('web_user_uid');
                localStorage.removeItem('web_user_name');
                localStorage.removeItem('web_user_pin');
            }

            document.getElementById('loading').classList.add('hidden');
            document.getElementById('success-screen').classList.remove('hidden');
        } else {
            customAlert((t('alert_error_create') || 'Error') + ': ' + (data.error || ''), '❌');
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('create-form').classList.remove('hidden');
        }
    } catch (error) {
        customAlert((t('alert_error_connection') || 'Connection Error') + ': ' + error, '❌');
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('create-form').classList.remove('hidden');
    }
}

function copyLink() {
    const link = document.getElementById('khatma-link').textContent;
    navigator.clipboard.writeText(link).then(() => {
        customAlert(t('alert_success_copy') || '✅ Link copied!', '✅');
    });
}

function goToKhatma() {
    window.location.href = `/${createdKhatmaId}`;
}

function showAdminLogin() {
    document.getElementById('welcome-screen').classList.add('hidden');
    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('admin-login').classList.remove('hidden');
}

async function adminLogin(event) {
    event.preventDefault();

    const adminName = document.getElementById('admin-login-name').value;
    const adminPin = document.getElementById('admin-login-pin').value;
    const khatmaId = document.getElementById('admin-khatma-id').value;

    document.getElementById('admin-login').classList.add('hidden');
    document.getElementById('loading').classList.remove('hidden');

    try {
        // Verify admin credentials
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: adminName,
                pin: adminPin,
                khatma_id: khatmaId
            })
        });

        const data = await response.json();

        if (data.success && data.is_admin) {
            // Store admin session
            localStorage.setItem('web_user_name', adminName);
            localStorage.setItem('web_user_pin', adminPin);
            localStorage.setItem('web_user_uid', data.uid);
            localStorage.setItem('is_admin', 'true');
            localStorage.setItem('admin_khatma_id', khatmaId);

            // Redirect to Khatma control panel
            window.location.href = `/${khatmaId}`;
        } else {
            customAlert((t('alert_error_login') || 'Invalid credentials') + ': ' + (data.error || ''), '❌');
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('admin-login').classList.remove('hidden');
        }
    } catch (error) {
        customAlert((t('alert_error_connection') || 'Connection Error') + ': ' + error, '❌');
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('admin-login').classList.remove('hidden');
    }
}

function shareLink() {
    const link = document.getElementById('khatma-link').textContent;
    if (navigator.share) {
        navigator.share({
            title: 'Khatma',
            text: 'Join our Quran Khatma',
            url: link
        }).catch(err => console.log('Share failed', err));
    } else {
        copyLink(); // Fallback
    }
}

function toggleAdminFields() {
    const enabled = document.getElementById('enable-admin').checked;
    const fields = document.getElementById('admin-fields');
    if (enabled) {
        fields.classList.remove('hidden');
        document.getElementById('admin-name').required = true;
        document.getElementById('admin-pin').required = true;
    } else {
        fields.classList.add('hidden');
        document.getElementById('admin-name').required = false;
        document.getElementById('admin-pin').required = false;
    }
}

function showWelcome() {
    document.getElementById('welcome-screen').classList.remove('hidden');
    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('success-screen').classList.add('hidden');
    document.getElementById('loading').classList.add('hidden');
}

function showCreateForm() {
    document.getElementById('welcome-screen').classList.add('hidden');
    document.getElementById('create-form').classList.remove('hidden');
}

async function createKhatma(event) {
    event.preventDefault();

    const khatmaName = document.getElementById('khatma-name').value;

    // Optional Admin Logic
    const enableAdmin = document.getElementById('enable-admin').checked;
    const adminName = enableAdmin ? document.getElementById('admin-name').value : null;
    const adminPin = enableAdmin ? document.getElementById('admin-pin').value : null;

    if (enableAdmin && (!adminName || !adminPin)) {
        return alert(translations[currentLang].alert_error_create || "Please fill admin details");
    }

    const intention = document.getElementById('intention').value;
    const deadline = document.getElementById('deadline').value;

    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('loading').classList.remove('hidden');

    try {
        const response = await fetch('/api/khatma/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: khatmaName,
                admin_name: adminName,
                admin_pin: adminPin,
                intention: intention || '',
                deadline: deadline || null
            })
        });

        const data = await response.json();

        if (data.success) {
            createdKhatmaId = data.khatma_id;
            const link = `${window.location.origin}/${createdKhatmaId}`;
            document.getElementById('khatma-link').textContent = link;

            // IMPORTANT: Store admin credentials for automatic login ONLY if admin was created
            if (data.admin_uid) {
                localStorage.setItem('web_user_name', adminName);
                localStorage.setItem('web_user_pin', adminPin);
                localStorage.setItem('web_user_uid', data.admin_uid);
                localStorage.setItem('is_admin', 'true');
                localStorage.setItem('admin_khatma_id', createdKhatmaId);
            } else {
                // Clear any previous admin session to avoid confusion
                localStorage.removeItem('is_admin');
                localStorage.removeItem('admin_khatma_id');
                localStorage.removeItem('web_user_uid');
                localStorage.removeItem('web_user_name');
                localStorage.removeItem('web_user_pin');
            }

            document.getElementById('loading').classList.add('hidden');
            document.getElementById('success-screen').classList.remove('hidden');
        } else {
            alert((translations[currentLang].alert_error_create || 'Error') + ': ' + (data.error || ''));
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('create-form').classList.remove('hidden');
        }
    } catch (error) {
        alert((translations[currentLang].alert_error_connection || 'Connection Error') + ': ' + error);
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('create-form').classList.remove('hidden');
    }
}

function copyLink() {
    const link = document.getElementById('khatma-link').textContent;
    navigator.clipboard.writeText(link).then(() => {
        alert(translations[currentLang].alert_success_copy || '✅ Link copied!');
    });
}

function goToKhatma() {
    window.location.href = `/${createdKhatmaId}`;
}

function showAdminLogin() {
    document.getElementById('welcome-screen').classList.add('hidden');
    document.getElementById('create-form').classList.add('hidden');
    document.getElementById('admin-login').classList.remove('hidden');
}

async function adminLogin(event) {
    event.preventDefault();

    const adminName = document.getElementById('admin-login-name').value;
    const adminPin = document.getElementById('admin-login-pin').value;
    const khatmaId = document.getElementById('admin-khatma-id').value;

    document.getElementById('admin-login').classList.add('hidden');
    document.getElementById('loading').classList.remove('hidden');

    try {
        // Verify admin credentials
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: adminName,
                pin: adminPin,
                khatma_id: khatmaId
            })
        });

        const data = await response.json();

        if (data.success && data.is_admin) {
            // Store admin session
            localStorage.setItem('web_user_name', adminName);
            localStorage.setItem('web_user_pin', adminPin);
            localStorage.setItem('web_user_uid', data.uid);
            localStorage.setItem('is_admin', 'true');
            localStorage.setItem('admin_khatma_id', khatmaId);

            // Redirect to Khatma control panel
            window.location.href = `/${khatmaId}`;
        } else {
            alert((translations[currentLang].alert_error_login || 'Invalid credentials') + ': ' + (data.error || ''));
            document.getElementById('loading').classList.add('hidden');
            document.getElementById('admin-login').classList.remove('hidden');
        }
    } catch (error) {
        alert((translations[currentLang].alert_error_connection || 'Connection Error') + ': ' + error);
        document.getElementById('loading').classList.add('hidden');
        document.getElementById('admin-login').classList.remove('hidden');
    }
}

function shareLink() {
    const link = document.getElementById('khatma-link').textContent;
    if (navigator.share) {
        navigator.share({
            title: 'Khatma',
            text: 'Join our Quran Khatma',
            url: link
        }).catch(err => console.log('Share failed', err));
    } else {
        copyLink(); // Fallback
    }
}

function toggleAdminFields() {
    const enabled = document.getElementById('enable-admin').checked;
    const fields = document.getElementById('admin-fields');
    if (enabled) {
        fields.classList.remove('hidden');
        document.getElementById('admin-name').required = true;
        document.getElementById('admin-pin').required = true;
    } else {
        fields.classList.add('hidden');
        document.getElementById('admin-name').required = false;
        document.getElementById('admin-pin').required = false;
    }
}
