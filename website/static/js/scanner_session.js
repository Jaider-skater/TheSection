(function () {
    const STAY_KEY = 'scannerInternalNav';

    function isScannerStayPath(path) {
        try {
            const url = new URL(path, window.location.origin);
            return url.pathname === '/verify' || url.pathname.startsWith('/verify/');
        } catch {
            return false;
        }
    }

    function markInternalNav() {
        sessionStorage.setItem(STAY_KEY, '1');
    }

    window.markScannerInternalNav = markInternalNav;

    document.addEventListener('click', (event) => {
        const anchor = event.target.closest('a[href]');
        if (!anchor) return;
        if (isScannerStayPath(anchor.getAttribute('href'))) {
            markInternalNav();
        }
    });

    document.querySelectorAll('form[action="/verify/logout"]').forEach((form) => {
        form.addEventListener('submit', () => markInternalNav());
    });

    window.addEventListener('pagehide', () => {
        if (sessionStorage.getItem(STAY_KEY) === '1') {
            sessionStorage.removeItem(STAY_KEY);
            return;
        }
        fetch('/verify/logout', {
            method: 'POST',
            keepalive: true,
            credentials: 'same-origin',
            headers: { 'X-Scanner-Logout': '1' },
        });
    });
})();
