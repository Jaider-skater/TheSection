let quantity = 1;
let ticketType = 'general';
let memberStatus = { logged_in: false, bundle_min: 3, bundle_discount_percent: 15 };
let pricing = null;
const DOOR_FEE_DOLLARS = 5;

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
    const banner = document.getElementById('member-banner');
    const text = document.getElementById('member-banner-text');
    if (!banner || !text) return;

    if (memberStatus.logged_in) {
        banner.classList.remove('hidden');
        text.textContent = `${memberStatus.bundle_discount_percent}% off when you buy ${memberStatus.bundle_min}+ tickets.`;
    } else {
        banner.classList.add('hidden');
    }
}

function doorPriceLabel(priceCents) {
    const doorTotal = (priceCents / 100) + DOOR_FEE_DOLLARS;
    return `$${doorTotal % 1 === 0 ? doorTotal : doorTotal.toFixed(2)} door`;
}

function updateTypePriceLabels() {
    const types = memberStatus.ticket_types || {};
    const ga = document.getElementById('ga-price-label');
    const vip = document.getElementById('vip-price-label');
    const gaDoor = document.getElementById('ga-door-label');
    const vipDoor = document.getElementById('vip-door-label');
    if (ga && types.general) ga.textContent = formatDollars(types.general.price_cents);
    if (vip && types.vip) vip.textContent = formatDollars(types.vip.price_cents);
    if (gaDoor && types.general) gaDoor.textContent = doorPriceLabel(types.general.price_cents);
    if (vipDoor && types.vip) vipDoor.textContent = doorPriceLabel(types.vip.price_cents);
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
    const discountNote = document.getElementById('discount-note');

    if (pricing) {
        if (totalDisplay) totalDisplay.textContent = formatDollars(pricing.total_cents);

        if (discountNote) {
            if (pricing.legacy_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.textContent = `Legacy bundle: ${pricing.bundle_discount_percent}% off applied (${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each)`;
            } else if (memberStatus.logged_in && quantity < pricing.bundle_min) {
                discountNote.classList.remove('hidden');
                discountNote.textContent = `Add ${pricing.bundle_min - quantity} more for ${pricing.bundle_discount_percent}% legacy member discount`;
            } else {
                discountNote.classList.add('hidden');
            }
        }
    } else {
        const fallback = ticketType === 'vip' ? 25 : 10;
        if (totalDisplay) totalDisplay.textContent = '$' + (fallback * quantity);
        if (discountNote) discountNote.classList.add('hidden');
    }
}

function changeQuantity(change) {
    quantity = Math.max(1, quantity + change);
    refreshPricing();
}

async function createCheckoutSession() {
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

function showTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.classList.remove('hidden');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.style.transition = 'opacity 0.3s ease-out';
        modal.style.opacity = '1';
    }, 10);
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

loadMemberStatus().then(() => {
    updateTypeButtons();
    refreshPricing();
});
