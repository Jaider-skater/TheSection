let quantity = 1;
let ticketType = 'general';
let memberStatus = {
    logged_in: false,
    bundle_min: 4,
    bundle_discount_percent: 10,
    vip_bundle_min: 5,
    vip_bulk_discount_percent: 10,
};
let pricing = null;
let memberDiscountApplied = false;

function formatDollars(cents) {
    return '$' + (cents / 100).toFixed(cents % 100 === 0 ? 0 : 2);
}

function bulkPctForType(type) {
    const t = type || ticketType;
    if (t === 'vip') {
        return pricing?.bundle_discount_percent
            || memberStatus.vip_bulk_discount_percent
            || 10;
    }
    return pricing?.bundle_discount_percent
        || memberStatus.bundle_discount_percent
        || 10;
}

function bulkMinForType(type) {
    const t = type || ticketType;
    if (t === 'vip') {
        return pricing?.vip_bundle_min || memberStatus.vip_bundle_min || 5;
    }
    return pricing?.bundle_min || memberStatus.bundle_min || 4;
}

function formatBulkPricingLabel() {
    const gaMin = memberStatus.bundle_min || 4;
    const gaPct = memberStatus.bundle_discount_percent || 10;
    const vipMin = memberStatus.vip_bundle_min || 5;
    const vipPct = memberStatus.vip_bulk_discount_percent || 10;
    return `${gaMin}+ GA ${gaPct}% off · ${vipMin}+ VIP ${vipPct}% off`;
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

function memberCodeHintText() {
    const memberPct = memberStatus.member_discount_percent;
    const bulkPct = bulkPctForType();
    if (!memberDiscountApplied) {
        if (pricing && pricing.bundle_discount_applied) {
            return `Bulk pricing active — tap to add ${memberPct}% member (${bulkPct + memberPct}% total)`;
        }
        return `Tap to add ${memberPct}% member discount`;
    }
    if (pricing && pricing.stacked_discount_applied) {
        const totalPct = pricing.combined_discount_percent || (bulkPct + memberPct);
        return `${totalPct}% off (${bulkPct}% bulk + ${memberPct}% member)`;
    }
    if (pricing && pricing.member_discount_applied) {
        return `${memberPct}% member discount applied`;
    }
    return `${memberPct}% member discount applied`;
}

function updateDiscountCodeButton() {
    const discountBtn = document.getElementById('member-discount-code-btn');
    const codeLabel = document.getElementById('member-discount-code-label');
    const codeHint = document.getElementById('member-discount-code-hint');
    if (!discountBtn || !codeLabel || !codeHint) return;

    if (memberStatus.logged_in && memberStatus.member_discount_eligible && memberStatus.discount_code) {
        discountBtn.classList.remove('hidden');
        codeLabel.textContent = memberStatus.discount_code;
        if (memberDiscountApplied) {
            discountBtn.classList.add('border-white', 'bg-zinc-950');
            discountBtn.classList.remove('border-zinc-700');
        } else {
            discountBtn.classList.remove('border-white', 'bg-zinc-950');
            discountBtn.classList.add('border-zinc-700');
        }
        codeHint.textContent = memberCodeHintText();
        return;
    }

    discountBtn.classList.add('hidden');
    memberDiscountApplied = false;
}

function toggleMemberDiscount() {
    if (!memberStatus.member_discount_eligible || !memberStatus.discount_code) return;
    memberDiscountApplied = !memberDiscountApplied;
    updateDiscountCodeButton();
    refreshPricing();
}

function updateMemberBanner() {
    const signedInBanner = document.getElementById('member-banner');
    const signInPrompt = document.getElementById('sign-in-prompt');

    const discountLine = document.getElementById('member-discount-line');
    const discountBtn = document.getElementById('member-discount-code-btn');
    if (memberStatus.logged_in) {
        if (signedInBanner) signedInBanner.classList.remove('hidden');
        if (signInPrompt) signInPrompt.classList.add('hidden');
        if (discountLine) {
            const bulkLabel = formatBulkPricingLabel();
            if (memberStatus.member_discount_eligible && memberStatus.discount_code) {
                discountLine.textContent = `Bulk pricing (${bulkLabel}) applies automatically. Tap your code below to stack another ${memberStatus.member_discount_percent}% off.`;
            } else {
                discountLine.textContent = `Bulk pricing: ${bulkLabel}. Member discount unlocks after your first purchase.`;
            }
        }
        updateDiscountCodeButton();
        if (discountBtn) {
            discountBtn.onclick = toggleMemberDiscount;
        }
    } else {
        if (signedInBanner) signedInBanner.classList.add('hidden');
        if (signInPrompt) signInPrompt.classList.remove('hidden');
        memberDiscountApplied = false;
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
    const vipLabel = document.getElementById('vip-type-label');
    if (!generalBtn || !vipBtn) return;

    generalBtn.classList.remove('border-white', 'bg-white', 'bg-black', 'text-black', 'text-white');
    generalBtn.classList.add('border-zinc-700', 'text-white');
    vipBtn.classList.remove('border-white', 'bg-white', 'bg-black', 'text-black', 'text-white');
    vipBtn.classList.add('border-zinc-700', 'text-white');
    if (vipLabel) {
        vipLabel.classList.remove('text-white');
        vipLabel.classList.add('text-zinc-300');
    }

    if (ticketType === 'vip') {
        vipBtn.classList.remove('border-zinc-700');
        vipBtn.classList.add('border-white', 'bg-black', 'text-white');
        if (vipLabel) {
            vipLabel.classList.remove('text-zinc-300');
            vipLabel.classList.add('text-white');
        }
    } else {
        generalBtn.classList.remove('border-zinc-700', 'text-white');
        generalBtn.classList.add('border-white', 'bg-white', 'text-black');
    }
}

async function refreshPricing() {
    try {
        let url = `/api/pricing?ticket_type=${ticketType}&quantity=${quantity}`;
        if (memberDiscountApplied) {
            url += '&apply_member_discount=1';
        }
        const response = await fetch(url);
        pricing = await response.json();
        updateModalQuantity();
        updateDiscountCodeButton();
    } catch (err) {
        console.error('Failed to load pricing', err);
        pricing = null;
        updateModalQuantity();
        updateDiscountCodeButton();
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
        const discountApplied = pricing.stacked_discount_applied
            || pricing.member_discount_applied
            || pricing.bundle_discount_applied;

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
            const bulkPct = bulkPctForType();
            const memberPct = pricing.member_discount_percent;
            const priceLine = `${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each`;

            if (pricing.stacked_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-white');
                discountNote.classList.remove('text-zinc-400', 'text-emerald-300');
                const totalPct = pricing.combined_discount_percent || (bulkPct + memberPct);
                discountNote.textContent = `${totalPct}% off (${bulkPct}% bulk + ${memberPct}% member) — ${priceLine}`;
            } else if (pricing.member_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-white');
                discountNote.classList.remove('text-zinc-400', 'text-emerald-300');
                discountNote.textContent = `${memberPct}% member discount — ${priceLine}`;
            } else if (pricing.bundle_discount_applied) {
                discountNote.classList.remove('hidden');
                discountNote.classList.add('text-white');
                discountNote.classList.remove('text-zinc-400', 'text-emerald-300');
                discountNote.textContent = `${bulkPct}% bulk pricing — ${priceLine}`;
            } else if (
                memberStatus.logged_in
                && memberStatus.member_discount_eligible
                && memberStatus.discount_code
                && !memberDiscountApplied
            ) {
                discountNote.classList.remove('hidden');
                discountNote.classList.remove('text-white', 'text-emerald-300');
                discountNote.classList.add('text-zinc-400');
                discountNote.textContent = `Tap ${memberStatus.discount_code} above to apply ${memberPct}% off`;
            } else if (quantity < bulkMinForType()) {
                discountNote.classList.remove('hidden');
                discountNote.classList.remove('text-white', 'text-emerald-300');
                discountNote.classList.add('text-zinc-400');
                const needed = bulkMinForType() - quantity;
                const verb = ticketType === 'vip' ? 'Buy' : 'Add';
                discountNote.textContent = `${verb} ${needed} more for ${bulkPct}% off`;
            } else if (memberStatus.logged_in && !memberStatus.member_discount_eligible) {
                discountNote.classList.remove('hidden');
                discountNote.classList.remove('text-white', 'text-emerald-300');
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

async function redirectToLoginForCheckout() {
    try {
        await fetch('/api/checkout-intent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                quantity: quantity,
                ticket_type: ticketType,
                apply_member_discount: memberDiscountApplied,
            }),
        });
    } catch (err) {
        console.error('Failed to save checkout intent', err);
    }
    window.location.href = '/legacy?next=' + encodeURIComponent('/checkout/resume');
}

async function createCheckoutSession() {
    if (!memberStatus.logged_in) {
        await redirectToLoginForCheckout();
        return;
    }

    try {
        const response = await fetch('/create-checkout-session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                quantity: quantity,
                ticket_type: ticketType,
                apply_member_discount: memberDiscountApplied,
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
