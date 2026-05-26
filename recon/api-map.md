# WiseGolf API map (Espoo Golf instance)

Captured live 2026-05-06 from `https://app.wisegolf.fi/#/golf/reservation/28`.
Stack: Vue 3 + Vuex + Capacitor (web target). HTTP client = `CapacitorHttp` plugin (proxies fetch on web).

## Hosts
- REST (1.0): `https://api.espoogolf.fi/api/1.0`
- Legacy AJAX: `https://ajax.espoogolf.fi/`
- App: `https://app.wisegolf.fi`

Per-club instance subdomain. URL fragment `#/golf/reservation/{productId}` selects course.

## Auth
- Bearer token in `Authorization: Bearer {token}` header.
- Stored in `localStorage["CapacitorStorage.access_token-affbfa03"]`.
- Capacitor storage prefix `CapacitorStorage.`, suffix `-affbfa03` is per-app instance id.
- `selectedHost` localStorage points to club slug, e.g. `espoogolf` → `api.{slug}.fi`.
- No CSRF token observed; `withCredentials: true` set.

## Response envelope
All REST 1.0 endpoints return:
```json
{ "success": true, "rows": [...], "errors": [], "statistics": {...}, "api": {"version":"1.0","sid":null} }
```

## Key endpoints

### Read (confirmed working)
- `GET /auth/session/?lang=...` — session check
- `GET /golf/player/`
  rows[].keys: `personId, firstName, familyName, playerId, memberNO, clubAbbreviation, clubId, ...`
  Plus `accessRights[]: {categoryId, personId, name, usableQuantity}` — playing rights.
- `GET /golf/club/` — club info
- `GET /products/?type=6&select=productid,meta,type,...`
  rows[].keys: `productId, type, ticketType, name, manufacturerCode, ...`. type=6 = bookable golf product.
- `GET /reservations/initialization/?productid={id}`
  Top: `success, REST_URL, loggedIn, enableUnregisteredOrdering, keepAdminReservationDate, hasPrivileges, user{userId,personId}`.
- `GET /reservations/calendarsettings/?productid={id}&date=YYYY-MM-DD`
  Top: `reservationSettings, resourceRules, golfProducts`.
- `GET /reservations/?productid={id}&date=YYYY-MM-DD&golf=1` — **TEE SHEET**
  rows[].keys: `reservationTimeId, dateCreated, resources[{resourceId,quantity}], start, end, status, quantity, isUserReservation, isSellable, shareId, label, overrideName, teetimeIcon`
  Top also has `reservationsGolfPlayers, reservationsAdditionalResources, resourceComments, fromStartup, duration`.
  - `start`, `end`: `"YYYY-MM-DD HH:MM:SS"` local time (Europe/Helsinki).
  - `isSellable`: only true once booking horizon opens (10:00 Helsinki, N days ahead).
  - `status`: 2/3/4 (open/closed/etc — to map).
- `GET /reservations/getusergolfreservations/` — my bookings (predicted from bundle)
- `GET /reservations/getuserreservations/` — generic my bookings
- `GET /carts/my/products` — my cart contents

### Write (need live recon to finalize payload)
- `POST /reservations/order/` — disable/lock slot. **Payload tbd**, likely:
  `{ productid, reservationTimeId, quantity, start, end }`
- `POST /carts/my/products` — add product to cart (qty)
- `POST /carts/pricecheck` — validate cart pricing
- `POST /carts/checkout` — finalize order
- `POST /reservations/confirmgolfteetime/` — confirm tee booking
- Players: store action `res_golf/saveTeetimePlayers` → likely POST somewhere with `{reservationTimeId, players:[personId]}`

### Cancel
- `POST /reservations/deactivatereservationtime/` — bundle exposes; payload tbd.
- Cart: `DELETE /carts/order` — remove pending cart order.

## Vuex store (Vue app instance at `document.querySelector('#app').__vue_app__`)
Modules: `user, common, door, gym_common, res_common, res_calendar, res_ecom, res_memberships, res_reservations, res_golf`.

Useful actions:
- `res_reservations/getReservations({productid, date})` — fetch tee sheet.
- `res_ecom/addToCart({productid, reservationTimeId, quantity, start, end})` — disable + add.
- `res_ecom/getMyCart`.
- `res_ecom/deleteCartOrder` — kill pending order.
- `res_golf/getUserGolfReservations` — my bookings.
- `res_golf/saveTeetimePlayers({reservationTimeId, players})` — assign players.
- `res_golf/saveGolfPlayerDetails`, `res_golf/saveVisitorPlayer`.

## Snipe behavior model
- Booking horizon opens at 10:00 Europe/Helsinki, N days ahead (N from `reservationSettings.bookingDaysInAdvance` or similar — verify).
- Pre-horizon: `isSellable: false`, `status` indicates closed.
- At 10:00 sharp: server flips slots for new day to `isSellable: true`. Race begins.
- Strategy: 
  1. Sleep until 10:00:00.000 Helsinki.
  2. Poll `GET /reservations/?productid=28&date={today+N}&golf=1` every ~250ms.
  3. First poll where target slot has `isSellable: true` → fire `POST /reservations/order/` immediately.
  4. Then add to cart, confirm tee time, save players (personId 37105 + 37125).

## TODO live recon (do at first real booking)
Patch `window.fetch` BEFORE Capacitor loads (via early script injection) or use Playwright with response logging to capture:
- `POST /reservations/order/` request body
- `POST /carts/my/products` request body + headers
- `POST /reservations/confirmgolfteetime/` request body
- `res_golf/saveTeetimePlayers` actual endpoint + body

## Known IDs
- productId (course): 28 (Espoon Golfseura)
- person 1: personId=37105, playerId=10349673, memberNO=16968, clubAbbr=Kurk
- person 2: personId=37125, playerId=92186071, memberNO=16899, clubAbbr=Kurk
- access category: 198 ("NUORISO2 pelioikeus 2026 EGS")
