# Khabar Cinema Studio

Reel upload karo → phone par joi lo → caption sudharo → **Approve** → Instagram + Facebook + YouTube par post.

- Halki website (Python Flask + SQLite). 1 GB DigitalOcean droplet par chale che.
- Password thi lock. Badha token fakt server par rahe che.
- Darek reel ni caption niche AI ni jankari jate umeray che. YouTube par "altered/synthetic content" label pan lage che.

---

## 1. Server par install (ek var)

DigitalOcean droplet (Ubuntu 22.04/24.04) ma **Console** kholo, ke `ssh root@TAMARO-IP`.

Aa repo **private** che, etle clone karti vakhte GitHub password ni jagya e **token** joishe:
1. github.com → profile photo → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. Repository access: **Only select repositories → khabar-cinema-studio**
3. Permissions → Repository → **Contents: Read-only** → Generate. Token copy karo (koi ne na aapo).

Pachhi server par:

```bash
cd /opt
git clone https://github.com/kamdar18/khabar-cinema-studio.git
# Username: kamdar18   Password: (upar no token paste karo)
cd khabar-cinema-studio
sudo bash install.sh
```

`install.sh` website no password puchhshe. Pachhi phone ma `http://TAMARO-IP:8090` kholo.

Website alag port **8090** par chale che. Server par chalta tamara bija projects (port 80/443) ne kai asar nathi thati, ane firewall pan chalu nathi karti. 8090 pehle thi vaparato hoy to: `PUBLIC_PORT=8091 sudo -E bash install.sh`.

DigitalOcean ma **Cloud Firewall** chalu hoy to tya inbound **TCP 8090** allow karjo.

**Update karva:** `cd /opt/khabar-cinema-studio && git pull && sudo bash install.sh`

---

## 2. Instagram + Facebook connect

1. **developers.facebook.com** → Log in → **My Apps → Create app** → use case: **Manage everything on your Page** (ke "Other → Business").
2. App ma products umero: **Instagram (API setup with Facebook login)** ane **Facebook Login for Business**.
3. **Tools → Graph API Explorer**:
   - Meta App: tamari app
   - Permissions: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`, `business_management`
   - **Generate Access Token** → Khabar Cinema Page pasand karo → Allow.
   - "User or Page" dropdown ma **Khabar Cinema** pasand karo → have je token dekhay e **Page token** che.
4. **Access Token Debugger** ma token kholo → **Extend Access Token** (lambo chalto token).
5. Website → ⚙️ Settings → **Page ID** (Facebook Page → About → Page transparency) ane **Page token** paste karo → **Save** → **Test connection**.

App "Development mode" ma rahe ane tame app na admin ho to tamara potana Page/Instagram par post thai shake che.

## 3. YouTube connect

1. **console.cloud.google.com** → navo project `khabar-cinema`.
2. **APIs & Services → Library → YouTube Data API v3 → Enable**.
3. **OAuth consent screen** → External → app naam, tamaro email → Test users ma tamaro Gmail umero.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → Client ID ane Client secret copy karo.
5. Website → Settings → YouTube ma paste → Save → **Connect YouTube** link → Khabar Cinema channel → Allow → "localhost" vala page nu aakhu address copy kari paste karo → Connect.

> Google audit pass na thay tya sudhi API thi upload thayela video **private** rahe che. YouTube Studio ma jai "Public" kari shakay, ke reel page parthi **Download** kari YouTube app thi upload karo. Audit form: Google Cloud Console → YouTube API Services audit.

---

## Rojnu kaam
1. **+ New** → MP4, caption, hashtags → Upload
2. Reel kholo → video jovo → jarur pade to text sudharo
3. Platform pasand karo → **✅ Approve & Post**
4. Koi platform fail thay to error dekhashe → **🔁 Fari try karo**

Disk bharai na jay e mate posted reels nu video "Server mathi delete" kari shakay (social media par post rahe).
