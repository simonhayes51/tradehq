# Railway Deployment Guide - Frontend

## 1. Create Frontend Service

1. Go to your Railway project (same project as backend)
2. Click **New** → **GitHub Repo**
3. Select `simonhayes51/frontend_new`

## 2. Configure Environment Variables

In Railway project settings → **Variables**, add:

```bash
# Backend API URL - FROM BACKEND RAILWAY URL
VITE_API_URL=https://your-backend-url.railway.app
VITE_SOCIAL_API_URL=https://your-backend-url.railway.app

# Discord (optional - for OAuth login link)
VITE_DISCORD_CLIENT_ID=your-discord-client-id

# Environment
VITE_ENV=production
NODE_ENV=production
```

## 3. Build Settings

Railway should auto-detect these, but verify in **Settings** → **Build**:

- **Build Command**: `npm run build`
- **Start Command**: `npx serve -s dist -l $PORT`
- **Root Directory**: `/`

## 4. Configure Domain

1. Go to **Settings** → **Domains**
2. Click **Generate Domain** for free `.railway.app` domain
3. Copy the URL (e.g., `https://your-app.railway.app`)

## 5. Update Backend FRONTEND_URL

Go back to **Backend** service → **Variables**:

```bash
FRONTEND_URL=https://your-frontend-url.railway.app
```

Redeploy backend for changes to take effect.

## 6. Update Discord OAuth Redirect

1. Go to Discord Developer Portal
2. Update OAuth2 redirect URL to include frontend:
   - `https://your-backend-url.railway.app/auth/callback`
   - Keep the backend URL for the actual OAuth callback

## 7. Verify Deployment

Visit your frontend URL and check:
- ✅ Page loads without errors
- ✅ Can navigate to different pages
- ✅ Login with Discord works
- ✅ API calls to backend succeed (check Network tab)

## 8. Setup Custom Domain (Optional)

### For Your Own Domain:

1. In Railway → **Settings** → **Domains**
2. Click **Custom Domain**
3. Enter your domain (e.g., `app.futhub.co.uk`)
4. Add DNS records at your domain registrar:

```
Type: CNAME
Name: app (or @)
Value: [Railway provided value]
```

5. Update environment variables in both services:

**Backend:**
```bash
FRONTEND_URL=https://app.futhub.co.uk
COOKIE_DOMAIN=.futhub.co.uk
```

**Frontend:**
```bash
VITE_API_URL=https://api.futhub.co.uk
```

## 9. Enable Production Optimizations

In **Settings** → **Build**:
- Ensure `NODE_ENV=production` is set
- This enables minification and optimizations

## 10. Monitor

- Check **Deployments** tab for build logs
- Use **Metrics** tab to monitor traffic
- Set up **Alerts** for downtime

## Troubleshooting

### Build Fails
```bash
# Check package.json scripts
# Ensure all dependencies in package.json
# Check build logs for missing modules
```

### Blank Page After Deploy
```bash
# Check browser console for errors
# Verify VITE_API_URL is correct
# Check CORS settings in backend
```

### API Calls Fail
```bash
# Verify VITE_API_URL includes https://
# Check backend logs for CORS errors
# Ensure backend FRONTEND_URL matches your frontend domain
```

### Images Don't Load
```bash
# Check public/ folder is committed to git
# Verify vite.config.js base path
# Use absolute paths for assets
```

## Post-Deployment Checklist

- [ ] Frontend loads successfully
- [ ] Backend API is accessible
- [ ] Discord OAuth login works
- [ ] Can create/view posts
- [ ] Subscription system works
- [ ] Content requests display
- [ ] Saved posts page works
- [ ] Tip system functional
- [ ] Mobile responsive
- [ ] All API endpoints respond correctly

## Rolling Back

If something goes wrong:

1. Go to **Deployments** tab
2. Find a previous successful deployment
3. Click **⋯** → **Rollback**

## Scaling

Railway auto-scales, but you can adjust:

1. **Settings** → **Resources**
2. Increase memory/CPU if needed
3. Monitor usage in **Metrics** tab
