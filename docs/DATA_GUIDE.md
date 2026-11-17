# IPDR data guide

What the table `wms_dist.ntc_ssl_log` holds, how its fields relate, and how questions map to them.
Every figure here was measured on the data (one full day, 9 Nov 2026, and the week 3–9 Nov). The AI
reads the same facts from `ipdr/semantic.yaml`; keep the two in step.

## What one row is

One row is one **connection** (a network flow) from a subscriber's device to one server: when it
started and ended, how many bytes and packets went each way, which app and website it was, which server
answered, and who the subscriber is. About **52.3 million rows a day**, the same every full day. The
first day (11 Oct) and the last (10 Nov) are partial.

## The things in the data

| Thing | Where it is | Facts that matter for questions |
|---|---|---|
| **Subscriber** (a person or account) | `subscriber_id` without `_` | About 120,000, the same ones every day. Each is active every day: about 200 connections and 70 apps a day, median 20 MB a day. Each has **one ISP, one address, one city and one fixed IP**, with rare exceptions: 923995974951 and 03353016943 carry two sets of details every day (two addresses; one also has two ISPs), so look-ups list every stored value. |
| **Unidentified record** | `subscriber_id` = `<client_ip>_<port>` | About half of all rows. Its IP never belongs to an identified subscriber, so it cannot be traced to a person. It counts as traffic, never as a user. |
| **Mobile number details** | `imsi`, `imei`, `mcc`, `mnc`, `lac`, `cid`, `usr_loc_info` | Only for numbers stored as `92…` (about 30% of rows). One SIM (IMSI) per number. 3.8% of handsets (IMEI) are used by more than one subscriber. |
| **ISP** | `client_asn` | The subscriber's network provider, filled on 98% of rows (about 270 names). |
| **SIM operator** | `mcc` / `mnc` | Only for mobile numbers. In this data it does not agree with the ISP. |
| **City** | `client_location` | `'<City> Pakistan'`. Special values: `Pakistan` = city unknown; `Punjab Pakistan`, `Sindh Pakistan`, `Khyber Pakhtunkhwa Pakistan` = province only; a few foreign places (Kabul, Tashkent, United States). Some names have accents (`Sādiqābād Pakistan`). |
| **IP version** | `client_ip` | IPv6 subscribers have a `:` in `client_ip` (about 15% of identified subscribers on a day). |
| **Address** | `loc`, `geo` | `geo` is `'lon,lat'` (longitude first). Always four parts: `House 1548, Street 29, Scheme 1, Islamabad Pakistan` or `Cell site 1846, Mall Road, Sector C, Islamabad Pakistan`. Part 3 is the area (10 names that repeat in every city: Civil Lines, Model Town, Town Ward, Scheme 1, Sector C, Phase 1, Phase 2, Block A, Block B, Satellite Town). `geo` holds the address's lon,lat; `client_geo` is just the city centre. |
| **App** | `app_label`, `app_category` | About 430 labels, each in exactly one of 19 categories. One app can have several labels (YouTube has 7; TikTok 5). SSL, GoogleAPI and similar are background traffic, not apps. |
| **Website** | `requested_server` | The hostname asked for (TLS SNI), about 7,500 a day; about 90% belong to a single app. Empty on about 8% of rows (QUIC video, WhatsApp, calls). |
| **Server** | `server_ip`, `server_port`, `server_asn`, `server_location`, `server_geo` | `server_asn` is who owns the server (Google LLC, Facebook, Inc.). `server_location` is `'City Country'` or just `'Country'`. Ports: 443 web, 853 DNS-over-TLS (AdGuard), 5228 Android push, 5223 Apple push, 993 mail (IMAP over SSL). |
| **Blocking** | `block_type` | 0 = allowed, 1–3 = blocked (1.3% of rows). Likely meanings (from the data, not from documentation): 1 = domain or app block (VK, Telegram, NordVPN, Xhamster: 86% of blocked rows); 2 = TCP drop on a few server IPs (QQ, Pornhub, Cloudflare ECH); 3 = UDP/QUIC block. VK, Telegram, NordVPN, Pornhub and Xhamster are blocked 100% of the time, and so are TrueCaller, Weather, Vshow, PoppoLive, SayHi, HotSpot, Xnxx and most VPN apps (Surfshark, PrivateVPN, Windscribe, Proton, Hola, Express). |
| **Time** | `start_time`, `end_time`, `recv_time`, `duration` | Unix seconds in UTC, shown in Pakistan time. `duration` is milliseconds (median 19 s). Calls are short records: the longest WhatsApp call record in a week is 71 s, IMO 46 s, Google Meet 18 min, Zoom 27 min. Busiest 18:00–21:00, quietest 01:00–04:00 (02:00 by users, 03:00 by connections). |
| **Probe** | `cap_ip` | The capture probe (55 of them, each sees every city). Not a place. |

Always empty, so they cannot answer anything: direction, stream_dir, action, l7_protocol, address_type,
schema_type, client_type, version, data_center, user_agent, all ssl_* fields, device_vendor, device_model,
is_roaming, sessions, first_ttl, establish_latency, processing_time, egress/ingress_link_id, device_id.

## Subscriber IDs

| Form | Example | Notes |
|---|---|---|
| Mobile number, international | `923869624946` | Has IMSI, IMEI and cell data. |
| Number, national | `03353016943` | A number is stored in **one** of the two forms, so a typed number is matched in both. |
| Account code | `AJR37134768`, `GBQJ05629017`, `292022gyxuk` | No SIM data; house address. |
| Unidentified | `100.65.228.1_30349` | Client IP and port: not a person. |

## Measures

| People say | Measure |
|---|---|
| how many people, users, log | identified subscribers: `uniqExactIf(subscriber_id, position(subscriber_id, '_') = 0)` |
| how much data, usage, traffic | GB: `sum(c2s_byte_num + s2c_byte_num) / 1e9`; upload = `c2s`, download = `s2c` |
| how many times, how often, visits | connections: `count()` |
| how long, time spent | `sum(duration) / 3.6e6` hours |
| top / heavy users (people) | one row per subscriber, ranked by data volume |
| most used apps (things) | users, then connections, plus volume |

Rankings are grouped by code before they run: a breakdown by ISP shows one row per brand (PTCL's three
stored names, and the two names each of Jazz, Connect, Transworld, Multinet and Worldcall, are added together),
and a breakdown by app shows one row per app (YouTube's five labels, TikTok's five, Facebook's six, WhatsApp's
four). Without this, "top ISPs by connections" left out Connect and "top apps in Multan" put WhatsApp above
YouTube.

User counts can't be added up across groups or shown as shares, because one person uses about 70 apps.
Because the data is synthetic, user counts often tie across apps and cities; connections then decide
the order.

## How the AI reads a question

Every question is broken into five parts, and the code checks each part against the SQL:

1. **Rows:** what one row of the answer is: an app, a subscriber, a city, an ISP, a website, an hour, a
   day, or a single total.
2. **Measure:** users, connections, volume, time, or stored details (address, ISP, IMSI…).
3. **Filters:** every name in the question. Names are matched to the stored values by code, ignoring case,
   accents and suffixes such as "Pvt Ltd": places, provinces, areas and roads, ISPs (brand names such as PTCL, Zong,
   Jazz and StormFiber are in `ipdr/isps.yaml`), apps and kinds of app, websites, server countries and owners, phone
   numbers, account codes, IPs, IMSI and IMEI. An answer that leaves a named filter out is sent back.
4. **Period:** a named period, otherwise the last 7 full days. A follow-up keeps the earlier period.
5. **Order and limit.**

Follow-ups such as "their addresses", "this user's ISP" or "inka imei" use the exact IDs from the
previous answer.

## Questions the data can answer (each has a worked example in `semantic.yaml`)

- Top apps, websites, ISPs or cities, by users, connections or volume, for any place, period or ISP.
- Lists of people: top users of an app, an ISP or a website; who visited a site; heavy uploaders; people
  who used two apps; people who tried blocked sites.
- One subscriber: stored details (address, ISP, city, IMSI, IMEI, IP), apps and websites used, an
  hour-by-hour timeline, first and last seen.
- Lookups: who had a given IP; which app a domain belongs to; which servers answer it and where.
- Devices: handsets shared by several subscribers; subscribers who changed handset.
- Servers: traffic to a country or a company's servers.
- Time: by hour (peak), by day, night only, one week against another.
- Areas: users per area inside a city.
- Shares: an ISP's share of a city's volume.

The data cannot answer questions about device models, roaming, SSL certificates or user agents (those
columns are empty), or about age, gender or income (not in the data).
