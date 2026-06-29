let quantity = 1;
let ticketType = 'general';
let memberStatus = { logged_in: false, bundle_min: 4, bundle_discount_percent: 25, vip_discount_percent: 10 };
let pricing = null;

function formatDollars(cents) {
    return '$' + (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2);
}

async function loadMemberStatus() {
    try {
        const response = await fetch('/api/member-status');
        memberStatus = await response.json();
        updateMemberBanner();
        updateTypePriceLabels();
    } catch (err) {
        console.error('Failed to load member status', err);
    }
}

function updateMemberBanner() {
    const signedInBanner = document.getElementById('member-banner');
    const signInPrompt = document.getElementById('sign-in-prompt');

    const discountLine = document.getElementById('member-discount-line');
    if (memberStatus.logged_in) {
        if (signedInBanner) signedInBanner.classList.remove('hidden');
        if (signInPrompt) signInPrompt.classList.add('hidden');
        if (discountLine) {
            if (memberStatus.member_discount_eligible && memberStatus.discount_code) {
                discountLine.textContent = `Code ${memberStatus.discount_code} · ${memberStatus.member_discount_percent}% off applied`;
            } else {
                const bulkPct = memberStatus.bundle_discount_percent;
                const vipPct = memberStatus.vip_discount_percent;
                const bulkMin = memberStatus.bundle_min;
                discountLine.textContent = `Bulk pricing at ${bulkMin}+ (${bulkPct}% GA / ${vipPct}% VIP). Member discount unlocks after your first purchase.`;
            }
        }
    } else {
        if (signedInBanner) signedInBanner.classList.add('hidden');
        if (signInPrompt) signInPrompt.classList.remove('hidden');
    }
}

function updateTypePriceLabels() {
    const types = memberStatus.ticket_types || {};
    const ga = document.getElementById('ga-price-label');
    const vip = document.getElementById('vip-price-label');
    if (ga && types.general) ga.textContent = formatDollars(types.general.price_cents);
    if (vip && types.vip) vip.textContent = formatDollars(types.vip.price_cents);
}

function updateTypeButtons() {
    const generalBtn = document.getElementById('type-general');
    const vipBtn = document.getElementById('type-vip');
    if (!generalBtn || !vipBtn) return;

    [generalBtn, vipBtn].forEach(btn => {
        btn.classList.remove('border-white', 'bg-white', 'text-black');
        btn.classList.add('border-zinc-700', 'text-white');
    });

    const active = ticketType === 'vip' ? vipBtn : generalBtn;
    active.classList.remove('border-zinc-700', 'text-white');
    active.classList.add('border-white', 'bg-white', 'text-black');
}

async function refreshPricing() {
    try {
        const response = await fetch(`/api/pricing?ticket_type=${ticketType}&quantity=${quantity}`);
        pricing = await response.json();
        updateModalQuantity();
    } catch (err) {
        console.error('Failed to load pricing', err);
        pricing = null;
        updateModalQuantity();
    }
}

function selectTicketType(type) {
    ticketType = type;
    updateTypeButtons();
    refreshPricing();
}

function updateModalQuantity() {
    document.getElementById('modal-quantity').textContent = quantity;

    const totalDisplay = document.getElementById('modal-total-price');
    const originalDisplay = document.getElementById('modal-original-price');
    const discountNote = document.getElementById('discount-note');

    if (pricing) {
        const discountApplied = pricing.member_discount_applied || pricing.vip_discount_applied || pricing.bundle_discount_applied;

        if (totalDisplay) totalDisplay.textContent = formatDollars(pricing.total_cents);

        if (originalDisplay) {
            if (discountApplied && pricing.base_total_cents > pricing.total_cents) {
                originalDisplay.textContent = formatDollars(pricing.base_total_cents);
                originalDisplay.classList.remove('hidden');
            } else {
                originalDisplay.classList.add('hidden');
            }
        }

        if (discountNote) {
            if (pricing.member_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-emerald-300');
                discountNote.classList.remove('text-zinc-400');
                discountNote.textContent = `${pricing.member_discount_percent}% member discount — ${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each`;
            } else if (pricing.vip_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-emerald-300');
                discountNote.classList.remove('text-zinc-400');
                discountNote.textContent = `${pricing.vip_discount_percent}% VIP discount — ${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each`;
            } else if (pricing.bundle_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-emerald-300');
                discountNote.classList.remove('text-zinc-400');
                discountNote.textContent = `${pricing.bundle_discount_percent}% off applied — ${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each`;
            } else if (quantity < pricing.bundle_min) {
                discountNote.classList.remove('hidden');
                discountNote.classList.remove('text-emerald-300');
                discountNote.classList.add('text-zinc-400');
                const pct = ticketType === 'vip'
                    ? pricing.vip_discount_percent
                    : pricing.bundle_discount_percent;
                discountNote.textContent = `Add ${pricing.bundle_min - quantity} more for ${pct}% off`;
            } else if (memberStatus.logged_in && !memberStatus.member_discount_eligible) {
                discountNote.classList.remove('hidden');
                discountNote.classList.remove('text-emerald-300');
                discountNote.classList.add('text-zinc-400');
                discountNote.textContent = 'Member discount unlocks after your first ticket purchase.';
            } else {
                discountNote.classList.add('hidden');
            }
        }
    } else {
        const fallback = ticketType === 'vip' ? 25 : 10;
        if (totalDisplay) totalDisplay.textContent = '$' + (fallback * quantity);
        if (originalDisplay) originalDisplay.classList.add('hidden');
        if (discountNote) discountNote.classList.add('hidden');
    }
}

function changeQuantity(change) {
    quantity = Math.max(1, quantity + change);
    refreshPricing();
}

async function createCheckoutSession() {
    if (!memberStatus.logged_in) {
        window.location.href = '/legacy?next=' + encodeURIComponent('/?open_tickets=1');
        return;
    }

    try {
        const response = await fetch('/create-checkout-session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                quantity: quantity,
                ticket_type: ticketType,
            }),
        });

        const data = await response.json();

        if (data.url) {
            window.location.href = data.url;
        } else {
            alert('Error: ' + (data.error || 'Something went wrong'));
        }
    } catch (err) {
        console.error(err);
        alert('Failed to connect to payment processor.');
    }
}

async function showTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.classList.remove('hidden');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.style.transition = 'opacity 0.3s ease-out';
        modal.style.opacity = '1';
    }, 10);
    await loadMemberStatus();
    refreshPricing();
}

function closeTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.classList.add('hidden');
    }, 300);
}

document.querySelectorAll('a[href="#tickets"]').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        showTicketsModal();
    });
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const modal = document.getElementById('tickets-modal');
        if (!modal.classList.contains('hidden')) closeTicketsModal();
    }
});

document.getElementById('tickets-modal').addEventListener('click', function(e) {
    if (e.target === this) closeTicketsModal();
});

const hamburgerBtn = document.getElementById('hamburger-btn');
const menuDropdown = document.getElementById('menu-dropdown');
if (hamburgerBtn && menuDropdown) {
    hamburgerBtn.addEventListener('click', () => {
        menuDropdown.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
        if (!hamburgerBtn.contains(e.target) && !menuDropdown.contains(e.target)) {
            menuDropdown.classList.add('hidden');
        }
    });
}

function maybeOpenTicketsFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('open_tickets') === '1') {
        showTicketsModal();
        params.delete('open_tickets');
        const nextQuery = params.toString();
        const nextUrl = window.location.pathname + (nextQuery ? `?${nextQuery}` : '') + window.location.hash;
        window.history.replaceState({}, '', nextUrl);
    }
}

loadMemberStatus().then(() => {
    updateTypeButtons();
    refreshPricing();
    maybeOpenTicketsFromUrl();
});
