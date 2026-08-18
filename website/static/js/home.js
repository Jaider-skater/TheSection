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
let ticketAvailability = window.ticketAvailability || {
    max_capacity: null,
    sold: null,
    remaining: null,
    sold_out: false,
};
let selectedEventId = (ticketAvailability && ticketAvailability.event_id) || (window.featuredEvent && window.featuredEvent.id) || '';
const MAX_TICKET_QUANTITY = 20;

function onSaleEventById(eventId) {
    const events = window.onSaleEvents || [];
    return events.find(event => event.id === eventId) || null;
}

function maxPurchasableQuantity() {
    const remaining = ticketAvailability && ticketAvailability.remaining;
    if (remaining == null) return MAX_TICKET_QUANTITY;
    return Math.max(0, Math.min(MAX_TICKET_QUANTITY, remaining));
}

function applyAvailabilityToUi() {
    const soldOut = !!(ticketAvailability && ticketAvailability.sold_out);
    const remaining = ticketAvailability ? ticketAvailability.remaining : null;
    const soldOutPanel = document.getElementById('sold-out-panel');
    const purchasePanel = document.getElementById('ticket-purchase-panel');
    const title = document.getElementById('tickets-modal-title');
    const subtitle = document.getElementById('tickets-modal-subtitle');
    const selected = onSaleEventById(selectedEventId) || window.featuredEvent;
    const eventName = (ticketAvailability && ticketAvailability.event_name) || (selected && selected.name) || 'Secure Your Spot';
    const eventDate = (ticketAvailability && ticketAvailability.event_date_display) || (selected && selected.date_display) || '';

    if (title) title.textContent = eventName;
    if (soldOutPanel) {
        soldOutPanel.classList.toggle('hidden', !soldOut);
    }
    if (purchasePanel) {
        purchasePanel.classList.toggle('hidden', soldOut);
    }
    if (subtitle) {
        if (soldOut) {
            subtitle.textContent = 'This event is sold out.';
        } else if (remaining != null && remaining > 0 && remaining <= 10) {
            subtitle.textContent = remaining === 1
                ? 'Only 1 ticket left.'
                : `Only ${remaining} tickets left.`;
        } else {
            subtitle.textContent = eventDate || 'Limited tickets available.';
        }
    }

    const signInLink = document.getElementById('sign-in-tickets-link');
    if (signInLink) {
        const next = selectedEventId
            ? `/?open_tickets=1&event_id=${encodeURIComponent(selectedEventId)}`
            : '/?open_tickets=1';
        signInLink.href = '/legacy?next=' + encodeURIComponent(next);
    }

    const cap = maxPurchasableQuantity();
    if (cap > 0 && quantity > cap) {
        quantity = cap;
    }
}

async function loadTicketAvailability(eventId) {
    try {
        const target = eventId || selectedEventId;
        const url = target
            ? `/api/ticket-availability?event_id=${encodeURIComponent(target)}`
            : '/api/ticket-availability';
        const response = await apiFetch(url);
        if (!response.ok) return;
        ticketAvailability = await response.json();
        if (ticketAvailability.event_id) selectedEventId = ticketAvailability.event_id;
        applyAvailabilityToUi();
        updateModalQuantity();
    } catch (err) {
        console.error('Failed to load ticket availability', err);
    }
}

function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function csrfHeaders(extra = {}) {
    const headers = { ...extra };
    const token = getCsrfToken();
    if (token) headers['X-CSRF-Token'] = token;
    return headers;
}

function apiFetch(url, options = {}) {
    // Always send same-origin cookies so login survives Stripe Checkout + Back.
    return fetch(url, {
        credentials: 'same-origin',
        ...options,
        headers: {
            ...(options.headers || {}),
        },
    });
}

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
        const statusUrl = selectedEventId
            ? `/api/member-status?event_id=${encodeURIComponent(selectedEventId)}`
            : '/api/member-status';
        const response = await apiFetch(statusUrl);
        memberStatus = await response.json();
        // Prefer CSRF token from response header if Flask rotated it.
        const headerToken = response.headers.get('X-CSRF-Token');
        if (headerToken) {
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta) meta.content = headerToken;
        }
        updateMemberBanner();
        updateTypePriceLabels();
    } catch (err) {
        console.error('Failed to load member status', err);
    }
}

function exclusiveSingleAvailable() {
    if (pricing && pricing.exclusive_single_available != null) {
        return !!pricing.exclusive_single_available;
    }
    if (memberStatus.exclusive_single_available != null) {
        return !!memberStatus.exclusive_single_available;
    }
    return !!(memberStatus.returning_guest_discount && quantity === 1);
}

function activeMemberPercent() {
    if (pricing && pricing.member_discount_percent) {
        return pricing.member_discount_percent;
    }
    if (exclusiveSingleAvailable() && quantity === 1) {
        return memberStatus.returning_guest_discount_percent || 20;
    }
    return memberStatus.member_discount_percent || 10;
}

function memberCodeHintText() {
    const memberPct = activeMemberPercent();
    const bulkPct = bulkPctForType();
    const isExclusiveSingle = exclusiveSingleAvailable() && quantity === 1;
    if (!memberDiscountApplied) {
        if (pricing && pricing.bundle_discount_applied) {
            return `Bulk pricing active — tap to add ${memberPct}% member (${bulkPct + memberPct}% total)`;
        }
        if (isExclusiveSingle) {
            return `Tap to add ${memberPct}% off this single ticket (one per event)`;
        }
        return `Tap to add ${memberPct}% member discount`;
    }
    if (pricing && pricing.stacked_discount_applied) {
        const totalPct = pricing.combined_discount_percent || (bulkPct + memberPct);
        return `${totalPct}% off (${bulkPct}% bulk + ${memberPct}% member)`;
    }
    if (pricing && pricing.member_discount_applied) {
        if (pricing.returning_guest_single_ticket_rate || isExclusiveSingle) {
            return `${memberPct}% exclusive single-ticket rate applied`;
        }
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
    const menuLogout = document.getElementById('menu-logout-form');

    const discountLine = document.getElementById('member-discount-line');
    const discountBtn = document.getElementById('member-discount-code-btn');
    if (memberStatus.logged_in) {
        if (signedInBanner) signedInBanner.classList.remove('hidden');
        if (signInPrompt) signInPrompt.classList.add('hidden');
        if (menuLogout) menuLogout.classList.remove('hidden');
        if (discountLine) {
            const bulkLabel = formatBulkPricingLabel();
            if (memberStatus.member_discount_eligible && memberStatus.discount_code) {
                if (memberStatus.returning_guest_discount) {
                    const welcome = memberStatus.returning_guest_discount_percent || 20;
                    const multi = memberStatus.member_discount_percent || 10;
                    if (exclusiveSingleAvailable()) {
                        discountLine.textContent = `${welcome}% off one ticket for this event — or ${multi}% when you buy 2+. Bulk (${bulkLabel}) can stack on multi-ticket orders. Tap your code below to apply.`;
                    } else {
                        discountLine.textContent = `You already used the ${welcome}% single-ticket rate for this event. ${multi}% member rate applies. Bulk (${bulkLabel}) can stack. Tap your code below to apply.`;
                    }
                } else {
                    discountLine.textContent = `Bulk pricing (${bulkLabel}) applies automatically. Tap your code below to stack another ${memberStatus.member_discount_percent}% off.`;
                }
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
        if (menuLogout) menuLogout.classList.add('hidden');
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
        if (selectedEventId) {
            url += `&event_id=${encodeURIComponent(selectedEventId)}`;
        }
        const response = await apiFetch(url);
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

function setPricingNote(el, visible, text, style) {
    if (!el) return;
    if (!visible) {
        el.classList.add('hidden');
        el.textContent = '';
        return;
    }
    el.classList.remove('hidden');
    el.textContent = text;
    el.classList.remove('text-white', 'text-zinc-400', 'text-emerald-300', 'font-semibold');
    if (style === 'bulk') {
        el.classList.add('text-white', 'font-semibold');
    } else if (style === 'applied') {
        el.classList.add('text-emerald-300', 'font-semibold');
    } else {
        el.classList.add('text-zinc-400');
    }
}

function updateModalQuantity() {
    document.getElementById('modal-quantity').textContent = quantity;

    const totalDisplay = document.getElementById('modal-total-price');
    const originalDisplay = document.getElementById('modal-original-price');
    const bulkNote = document.getElementById('bulk-pricing-note');
    const memberNote = document.getElementById('member-discount-note');

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

        const bulkPct = bulkPctForType();
        const memberPct = pricing.member_discount_percent || activeMemberPercent();
        const priceLine = `${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(pricing.unit_price_cents)} each`;
        const bulkOnlyUnit = Math.round(pricing.base_unit_price_cents * (1 - bulkPct / 100));
        const bulkOnlyLine = `${formatDollars(pricing.base_unit_price_cents)} → ${formatDollars(bulkOnlyUnit)} each`;

        let bulkText = '';
        let bulkVisible = false;
        if (pricing.bundle_discount_applied || pricing.stacked_discount_applied) {
            bulkVisible = true;
            bulkText = `${bulkPct}% bulk pricing — ${pricing.stacked_discount_applied ? bulkOnlyLine : priceLine}`;
        } else if (quantity < bulkMinForType()) {
            bulkVisible = true;
            const needed = bulkMinForType() - quantity;
            const verb = ticketType === 'vip' ? 'Buy' : 'Add';
            bulkText = `${verb} ${needed} more for ${bulkPct}% bulk pricing`;
        }
        setPricingNote(bulkNote, bulkVisible, bulkText, 'bulk');

        let memberText = '';
        let memberVisible = false;
        let memberStyle = 'prompt';
        if (pricing.stacked_discount_applied) {
            memberVisible = true;
            memberStyle = 'applied';
            const totalPct = pricing.combined_discount_percent || (bulkPct + memberPct);
            memberText = `+${memberPct}% member code applied — ${totalPct}% total (${priceLine})`;
        } else if (pricing.member_discount_applied) {
            memberVisible = true;
            memberStyle = 'applied';
            if (pricing.returning_guest_single_ticket_rate) {
                memberText = `${memberPct}% exclusive single-ticket rate — ${priceLine}`;
            } else {
                memberText = `${memberPct}% member discount — ${priceLine}`;
            }
        } else if (
            memberStatus.logged_in
            && memberStatus.member_discount_eligible
            && memberStatus.discount_code
            && !memberDiscountApplied
        ) {
            memberVisible = true;
            if (pricing.bundle_discount_applied) {
                memberText = `Tap ${memberStatus.discount_code} above to add ${memberPct}% more (→ ${bulkPct + memberPct}% total)`;
            } else if (memberStatus.returning_guest_discount && quantity === 1) {
                memberText = `Tap ${memberStatus.discount_code} for ${memberPct}% off this single ticket (one per event)`;
            } else {
                memberText = `Tap ${memberStatus.discount_code} above to apply ${memberPct}% off`;
            }
        } else if (memberStatus.logged_in && !memberStatus.member_discount_eligible) {
            memberVisible = true;
            memberText = 'Member discount unlocks after your first ticket purchase.';
        }
        setPricingNote(memberNote, memberVisible, memberText, memberStyle);
    } else {
        const fallback = ticketType === 'vip' ? 25 : 10;
        if (totalDisplay) totalDisplay.textContent = '$' + (fallback * quantity);
        if (originalDisplay) originalDisplay.classList.add('hidden');
        setPricingNote(bulkNote, false, '', 'bulk');
        setPricingNote(memberNote, false, '', 'prompt');
    }
}

function changeQuantity(change) {
    const cap = maxPurchasableQuantity();
    if (cap < 1) {
        quantity = 1;
        applyAvailabilityToUi();
        return;
    }
    quantity = Math.max(1, Math.min(cap, quantity + change));
    refreshPricing();
}

async function redirectToLoginForCheckout() {
    try {
        await apiFetch('/api/checkout-intent', {
            method: 'POST',
            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                quantity: quantity,
                ticket_type: ticketType,
                apply_member_discount: memberDiscountApplied,
                event_id: selectedEventId,
            }),
        });
    } catch (err) {
        console.error('Failed to save checkout intent', err);
    }
    window.location.href = '/legacy?next=' + encodeURIComponent('/checkout/resume');
}

async function createCheckoutSession() {
    if (ticketAvailability && ticketAvailability.sales_open === false) {
        alert('Tickets are not on sale for this event yet.');
        return;
    }
    if (ticketAvailability && ticketAvailability.sold_out) {
        applyAvailabilityToUi();
        alert('Tickets are sold out.');
        return;
    }
    if (!memberStatus.logged_in) {
        await redirectToLoginForCheckout();
        return;
    }

    try {
        const response = await apiFetch('/create-checkout-session', {
            method: 'POST',
            headers: csrfHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                quantity: quantity,
                ticket_type: ticketType,
                apply_member_discount: memberDiscountApplied,
                event_id: selectedEventId,
            }),
        });

        const data = await response.json();

        if (data.remaining != null || data.sold_out != null || data.max_capacity != null) {
            ticketAvailability = {
                max_capacity: data.max_capacity ?? ticketAvailability.max_capacity,
                sold: data.sold ?? ticketAvailability.sold,
                remaining: data.remaining ?? ticketAvailability.remaining,
                sold_out: !!(data.sold_out),
            };
            applyAvailabilityToUi();
        }

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

async function showTicketsModal(options = {}) {
    const eventId = options.eventId || selectedEventId;
    const event = onSaleEventById(eventId);
    if (!event || event.sales_open === false) {
        return;
    }
    selectedEventId = event.id;
    const modal = document.getElementById('tickets-modal');
    modal.classList.remove('hidden');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.style.transition = 'opacity 0.3s ease-out';
        modal.style.opacity = '1';
    }, 10);
    applyAvailabilityToUi();
    await Promise.all([loadMemberStatus(), loadTicketAvailability(event.id)]);
    if (ticketAvailability && ticketAvailability.sold_out) {
        applyAvailabilityToUi();
        return;
    }
    if (
        options.applyMemberDiscount
        && memberStatus.logged_in
        && memberStatus.member_discount_eligible
        && memberStatus.discount_code
    ) {
        memberDiscountApplied = true;
        updateDiscountCodeButton();
    }
    refreshPricing();
}

function closeTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.classList.add('hidden');
    }, 300);
}

document.querySelectorAll('.get-tickets-btn').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        showTicketsModal({ eventId: this.dataset.eventId });
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

function maybeOpenTicketsFromUrl() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('open_tickets') === '1') {
        const applyMemberDiscount = params.get('apply_member_discount') === '1';
        const eventId = params.get('event_id') || selectedEventId;
        params.delete('open_tickets');
        params.delete('apply_member_discount');
        params.delete('event_id');
        const nextQuery = params.toString();
        const nextUrl = window.location.pathname + (nextQuery ? `?${nextQuery}` : '') + window.location.hash;
        window.history.replaceState({}, '', nextUrl);
        showTicketsModal({ applyMemberDiscount, eventId });
    }
}

applyAvailabilityToUi();
Promise.all([loadMemberStatus(), loadTicketAvailability()]).then(() => {
    updateTypeButtons();
    refreshPricing();
    maybeOpenTicketsFromUrl();
});

// Returning from Stripe Checkout via Back often restores a cached page.
// Re-check login so the UI matches the session cookie.
window.addEventListener('pageshow', (event) => {
    if (event.persisted || (window.performance && performance.getEntriesByType &&
        performance.getEntriesByType('navigation')[0]?.type === 'back_forward')) {
        Promise.all([loadMemberStatus(), loadTicketAvailability()]).then(() => {
            updateTypeButtons();
            refreshPricing();
            updateDiscountCodeButton();
        });
    }
});
