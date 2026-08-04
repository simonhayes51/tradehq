# Stripe Webhook Handlers

This document provides implementation examples for Stripe webhook handlers needed for the subscription system.

## Webhook Endpoint Setup

### 1. Create Webhook Endpoint

In your Stripe Dashboard:
1. Go to Developers → Webhooks
2. Add endpoint: `https://api.futhub.co.uk/webhooks/stripe`
3. Select events to listen for:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`

### 2. Implementation (Python/Flask)

```python
# app/webhooks/stripe.py
import stripe
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
from app.models import User, db
from app.utils.email import send_subscription_email

bp = Blueprint('stripe_webhooks', __name__)
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
webhook_secret = os.environ.get('STRIPE_WEBHOOK_SECRET')

TIER_MAP = {
    'basic_monthly': 'basic',
    'basic_yearly': 'basic',
    'pro_monthly': 'pro',
    'pro_yearly': 'pro',
    'elite_monthly': 'elite',
    'elite_yearly': 'elite',
}

@bp.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    # Route to handler
    handlers = {
        'checkout.session.completed': handle_checkout_completed,
        'customer.subscription.created': handle_subscription_created,
        'customer.subscription.updated': handle_subscription_updated,
        'customer.subscription.deleted': handle_subscription_deleted,
        'invoice.payment_succeeded': handle_payment_succeeded,
        'invoice.payment_failed': handle_payment_failed,
    }

    handler = handlers.get(event['type'])
    if handler:
        try:
            handler(event['data']['object'])
        except Exception as e:
            # Log error but return 200 so Stripe doesn't retry
            print(f"Webhook handler error: {e}")
            # Send to Sentry/monitoring
            return jsonify({'error': str(e)}), 500

    return jsonify({'status': 'success'}), 200


def handle_checkout_completed(session):
    """Handle successful checkout - grant initial access"""
    user_id = session.get('client_reference_id')
    subscription_id = session.get('subscription')

    if not user_id:
        print(f"No user_id in session {session['id']}")
        return

    user = User.query.get(user_id)
    if not user:
        print(f"User {user_id} not found")
        return

    # Get subscription details
    subscription = stripe.Subscription.retrieve(subscription_id)
    price_id = subscription['items']['data'][0]['price']['id']

    # Map price_id to tier
    tier = get_tier_from_price_id(price_id)
    if not tier:
        print(f"Unknown price_id: {price_id}")
        return

    # Update user
    user.premium_tier = tier
    user.stripe_subscription_id = subscription_id
    user.stripe_customer_id = subscription['customer']
    user.premium_expires_at = datetime.fromtimestamp(
        subscription['current_period_end']
    )

    db.session.commit()

    # Send welcome email
    send_subscription_email(
        user.email,
        template='subscription_welcome',
        tier=tier,
        expires_at=user.premium_expires_at
    )

    print(f"User {user_id} upgraded to {tier}")


def handle_subscription_created(subscription):
    """Handle new subscription creation"""
    customer_id = subscription['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()

    if not user:
        print(f"No user found for customer {customer_id}")
        return

    price_id = subscription['items']['data'][0]['price']['id']
    tier = get_tier_from_price_id(price_id)

    user.premium_tier = tier
    user.stripe_subscription_id = subscription['id']
    user.premium_expires_at = datetime.fromtimestamp(
        subscription['current_period_end']
    )

    db.session.commit()
    print(f"Subscription created for user {user.id}: {tier}")


def handle_subscription_updated(subscription):
    """Handle subscription changes (upgrade/downgrade/renewal)"""
    customer_id = subscription['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()

    if not user:
        print(f"No user found for customer {customer_id}")
        return

    # Check if subscription is still active
    if subscription['status'] in ['active', 'trialing']:
        price_id = subscription['items']['data'][0]['price']['id']
        tier = get_tier_from_price_id(price_id)

        user.premium_tier = tier
        user.premium_expires_at = datetime.fromtimestamp(
            subscription['current_period_end']
        )
    elif subscription['status'] in ['canceled', 'unpaid', 'past_due']:
        # Downgrade to free (or grace period)
        user.premium_tier = 'free'

    db.session.commit()
    print(f"Subscription updated for user {user.id}: {user.premium_tier}")


def handle_subscription_deleted(subscription):
    """Handle subscription cancellation"""
    customer_id = subscription['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()

    if not user:
        return

    # Downgrade to free
    user.premium_tier = 'free'
    user.stripe_subscription_id = None
    user.premium_expires_at = None

    db.session.commit()

    # Send cancellation email
    send_subscription_email(
        user.email,
        template='subscription_cancelled'
    )

    print(f"Subscription cancelled for user {user.id}")


def handle_payment_succeeded(invoice):
    """Handle successful recurring payment"""
    customer_id = invoice['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()

    if not user:
        return

    # Extend subscription period
    subscription = stripe.Subscription.retrieve(invoice['subscription'])
    user.premium_expires_at = datetime.fromtimestamp(
        subscription['current_period_end']
    )

    db.session.commit()

    # Send receipt email
    send_subscription_email(
        user.email,
        template='payment_receipt',
        amount=invoice['amount_paid'] / 100,  # Convert cents to dollars
        invoice_url=invoice['hosted_invoice_url']
    )

    print(f"Payment succeeded for user {user.id}")


def handle_payment_failed(invoice):
    """Handle failed payment"""
    customer_id = invoice['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()

    if not user:
        return

    # Send payment failed email
    send_subscription_email(
        user.email,
        template='payment_failed',
        invoice_url=invoice['hosted_invoice_url']
    )

    # Optionally: Set grace period before downgrading
    # user.premium_grace_until = datetime.now() + timedelta(days=3)

    print(f"Payment failed for user {user.id}")


def get_tier_from_price_id(price_id):
    """Map Stripe price ID to tier"""
    price_map = {
        os.environ.get('STRIPE_BASIC_MONTHLY_PRICE_ID'): 'basic',
        os.environ.get('STRIPE_BASIC_YEARLY_PRICE_ID'): 'basic',
        os.environ.get('STRIPE_PRO_MONTHLY_PRICE_ID'): 'pro',
        os.environ.get('STRIPE_PRO_YEARLY_PRICE_ID'): 'pro',
        os.environ.get('STRIPE_ELITE_MONTHLY_PRICE_ID'): 'elite',
        os.environ.get('STRIPE_ELITE_YEARLY_PRICE_ID'): 'elite',
    }
    return price_map.get(price_id)
```

## Testing Webhooks Locally

### Using Stripe CLI

1. **Install Stripe CLI:**
   ```bash
   brew install stripe/stripe-cli/stripe
   # or
   wget https://github.com/stripe/stripe-cli/releases/download/v1.19.0/stripe_1.19.0_linux_x86_64.tar.gz
   ```

2. **Login:**
   ```bash
   stripe login
   ```

3. **Forward webhooks to local server:**
   ```bash
   stripe listen --forward-to localhost:5000/webhooks/stripe
   ```

4. **Trigger test events:**
   ```bash
   # Test checkout completion
   stripe trigger checkout.session.completed

   # Test subscription creation
   stripe trigger customer.subscription.created

   # Test payment success
   stripe trigger invoice.payment_succeeded

   # Test payment failure
   stripe trigger invoice.payment_failed
   ```

### Manual Testing with curl

```bash
# Generate webhook signature
SECRET="whsec_test_secret"
PAYLOAD='{"id":"evt_test","object":"event","type":"customer.subscription.created"}'
TIMESTAMP=$(date +%s)
SIGNATURE=$(echo -n "$TIMESTAMP.$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | cut -d' ' -f2)

curl -X POST http://localhost:5000/webhooks/stripe \
  -H "Content-Type: application/json" \
  -H "Stripe-Signature: t=$TIMESTAMP,v1=$SIGNATURE" \
  -d "$PAYLOAD"
```

## Webhook Security

### 1. Always Verify Signatures
```python
# GOOD - Verify signature
event = stripe.Webhook.construct_event(
    payload, sig_header, webhook_secret
)

# BAD - Never trust raw payload
event = json.loads(request.data)  # ❌ Insecure!
```

### 2. Use Idempotency
```python
# Store processed webhook IDs to prevent duplicate processing
from app.models import ProcessedWebhook

@bp.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    # ... verify signature ...

    event_id = event['id']

    # Check if already processed
    if ProcessedWebhook.query.filter_by(event_id=event_id).first():
        return jsonify({'status': 'already_processed'}), 200

    # Process event
    handler(event['data']['object'])

    # Mark as processed
    db.session.add(ProcessedWebhook(event_id=event_id))
    db.session.commit()

    return jsonify({'status': 'success'}), 200
```

### 3. Return 200 Even on Errors
```python
# Stripe retries failed webhooks exponentially
# Only return non-200 for truly unrecoverable errors
try:
    handler(event['data']['object'])
except TemporaryError as e:
    # Log and return 500 so Stripe retries
    return jsonify({'error': str(e)}), 500
except PermanentError as e:
    # Log but return 200 so Stripe doesn't retry forever
    sentry.capture_exception(e)
    return jsonify({'status': 'logged'}), 200
```

## Monitoring

### Track Important Metrics

```python
from prometheus_client import Counter, Histogram

webhook_counter = Counter(
    'stripe_webhooks_total',
    'Total Stripe webhooks received',
    ['event_type', 'status']
)

webhook_duration = Histogram(
    'stripe_webhook_duration_seconds',
    'Time to process webhook',
    ['event_type']
)

@bp.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    start_time = time.time()
    event_type = event.get('type', 'unknown')

    try:
        # ... process webhook ...
        webhook_counter.labels(event_type=event_type, status='success').inc()
    except Exception as e:
        webhook_counter.labels(event_type=event_type, status='error').inc()
        raise
    finally:
        duration = time.time() - start_time
        webhook_duration.labels(event_type=event_type).observe(duration)
```

## Common Issues

### Issue: Webhooks arrive out of order
**Solution:** Use `created` timestamp to determine order
```python
if event['created'] < user.last_webhook_timestamp:
    print("Ignoring old webhook")
    return
user.last_webhook_timestamp = event['created']
```

### Issue: User not found during checkout
**Solution:** Pass `client_reference_id` when creating checkout session
```python
session = stripe.checkout.Session.create(
    client_reference_id=str(user.id),  # ← Critical!
    # ...
)
```

### Issue: Subscription status not updating
**Solution:** Listen to `customer.subscription.updated` not just `created`
