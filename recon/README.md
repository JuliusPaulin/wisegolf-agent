# Recon notes

Fill after capturing live traffic from app.wisegolf.fi while logged in.

## Endpoints to capture
- POST login
- GET tee sheet (course 28, given date)
- POST create reservation
- GET my reservations
- DELETE / POST cancel

## Per endpoint record
- method + path
- query params
- request headers (esp auth: cookie name, Authorization, X-CSRF-Token)
- request body schema
- response body schema
- status codes seen

Save raw HAR to recon/wisegolf.har (gitignored).
