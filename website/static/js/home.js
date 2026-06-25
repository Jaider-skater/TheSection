// ==================== HAMBURGER MENU ====================
const hamburgerBtn = document.getElementById('hamburger-btn');
const menuDropdown = document.getElementById('menu-dropdown');

hamburgerBtn.addEventListener('click', () => {
    menuDropdown.classList.toggle('hidden');
});

document.addEventListener('click', (e) => {
    if (!hamburgerBtn.contains(e.target) && !menuDropdown.contains(e.target)) {
        menuDropdown.classList.add('hidden');
    }
});

// ==================== DARK MODE + LOGO SWITCHING ====================
const darkModeToggle = document.getElementById('dark-mode-toggle');
const darkModeIcon = document.getElementById('dark-mode-icon');
const darkModeText = document.getElementById('dark-mode-text');
const html = document.documentElement;

const headerLogo = document.getElementById('site-logo');
const heroLogo = document.getElementById('hero-logo');

const lightLogo = "/static/images/TheSectionLogo.png";
const darkLogo = "/static/images/TheSectionLogoDark.png";

function updateLogos(isDark) {
    const src = isDark ? darkLogo : lightLogo;

    if (headerLogo) headerLogo.src = src;
    if (heroLogo) heroLogo.src = src;

    console.log("Dark mode:", isDark, "→ Using:", src); // Debug log
}

function updateDarkModeUI(isDark) {
    if (isDark) {
        darkModeIcon.textContent = '☀️';
        darkModeText.textContent = 'Light Mode';
    } else {
        darkModeIcon.textContent = '🌙';
        darkModeText.textContent = 'Dark Mode';
    }
}

function loadTheme() {
    const isDark = localStorage.getItem('theme') === 'dark' || 
                   (!localStorage.getItem('theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);

    if (isDark) html.classList.add('dark');
    else html.classList.remove('dark');

    updateDarkModeUI(isDark);
    updateLogos(isDark);
}

darkModeToggle.addEventListener('click', () => {
    const isDark = html.classList.contains('dark');

    if (isDark) {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
    } else {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
    }

    updateDarkModeUI(!isDark);
    updateLogos(!isDark);
    menuDropdown.classList.add('hidden');
});

loadTheme();