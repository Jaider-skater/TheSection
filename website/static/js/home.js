// ==================== CUSTOM SMOOTH SCROLL ====================
function smoothScrollWithPause(target1, target2, duration1 = 1200, duration2 = 600) {
    const el1 = document.getElementById(target1);
    const el2 = document.getElementById(target2);
    if (!el1) return;

    const startY = window.scrollY;
    const targetY1 = el1.getBoundingClientRect().top + window.scrollY - 80;
    const targetY2 = el2 ? el2.getBoundingClientRect().top + window.scrollY - 100 : targetY1;

    let startTime = null;

    function animation(time) {
        if (!startTime) startTime = time;
        const elapsed = time - startTime;

        let progress = Math.min(elapsed / duration1, 1);
        const ease = 1 - Math.pow(1 - progress, 4); // very smooth slow-down

        let currentY = startY + (targetY1 - startY) * ease;

        // After reaching flyer, continue to tickets with slight speedup
        if (progress >= 1 && targetY2 !== targetY1) {
            const remainingTime = Math.min((elapsed - duration1) / duration2, 1);
            const remainingEase = remainingTime * remainingTime; // quadratic speed up
            currentY = targetY1 + (targetY2 - targetY1) * remainingEase;
        }

        window.scrollTo(0, currentY);

        if (elapsed < duration1 + duration2) {
            requestAnimationFrame(animation);
        }
    }

    requestAnimationFrame(animation);
}

async function createCheckoutSession() {
    try {
        const response = await fetch('/create-checkout-session', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                quantity: quantity
            })
        });

        const data = await response.json();

        if (data.url) {
            window.location.href = data.url;
        } else {
            alert("Error: " + (data.error || "Something went wrong"));
        }
    } catch (err) {
        console.error(err);
        alert("Failed to connect to payment processor.");
    }
}
let quantity = 1;

function updateModalQuantity() {
    document.getElementById('modal-quantity').textContent = quantity;
    document.getElementById('modal-total-price').textContent = '$' + (quantity * 10);
}

function changeQuantity(change) {
    quantity = Math.max(1, quantity + change);
    updateModalQuantity();
}

function showTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.classList.remove('hidden');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.style.transition = 'opacity 0.3s ease-out';
        modal.style.opacity = '1';
    }, 10);
}

function closeTicketsModal() {
    const modal = document.getElementById('tickets-modal');
    modal.style.opacity = '0';
    setTimeout(() => {
        modal.classList.add('hidden');
    }, 300);
}

// Open modal on Get Tickets click
document.querySelectorAll('a[href="#tickets"]').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        showTicketsModal();
    });
});

// Keyboard + backdrop support
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        const modal = document.getElementById('tickets-modal');
        if (!modal.classList.contains('hidden')) closeTicketsModal();
    }
});

document.getElementById('tickets-modal').addEventListener('click', function(e) {
    if (e.target === this) closeTicketsModal();
});

updateModalQuantity();