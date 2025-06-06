# Email Configuration

This project uses django-anymail with Postmark for email delivery.

## Environment Variables

Set the following environment variable in production:

```bash
POSTMARK_SERVER_TOKEN=your-postmark-server-token
```

## Development

In development, emails are printed to the console using Django's console email backend.

## Production

In production, emails are sent via Postmark. Make sure to:

1. Create a Postmark account
2. Verify your sending domain (civic.observer)
3. Get your Server API Token
4. Set the `POSTMARK_SERVER_TOKEN` environment variable

## Email Templates

Email templates are located in `templates/email/`:

- `search_update.txt` - Plain text version of search notification emails
- `search_update.html` - HTML version of search notification emails

## Testing Email

To test email functionality in development:

```python
from django.core.mail import send_mail
from django.conf import settings

send_mail(
    "Test Subject",
    "Test message body",
    settings.DEFAULT_FROM_EMAIL,
    ["recipient@example.com"],
    fail_silently=False,
)
```

## Postmark Message Streams

The project is configured to use Postmark's "outbound" message stream for transactional emails. This is set automatically in the SavedSearch model's `send_search_notification` method.
