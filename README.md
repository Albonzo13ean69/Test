# ClipNest — GitHub Pages + backend

The root `index.html` is the GitHub Pages website. The `backend/` folder is the Flask + yt-dlp service that does the actual download work. GitHub Pages cannot run Python, so both pieces are required for platform downloads.

## 1. Publish the website with GitHub Pages
1. Create a GitHub repository and upload the contents of this folder (make sure `index.html` is in the repository root).
2. Open **Settings → Pages**.
3. Under **Build and deployment**, choose **Deploy from a branch**, select `main` and `/(root)`, then save.
4. Note the website origin. It is the scheme + hostname only, such as `https://YOUR-USERNAME.github.io` (for a project site, the path after the hostname is not part of the origin).

## 2. Deploy the backend on Render
1. In Render, choose **New → Blueprint** and connect this same repository; Render will read `render.yaml`.
2. When prompted for `FRONTEND_ORIGIN`, enter the origin of your GitHub Pages site, e.g. `https://YOUR-USERNAME.github.io`. Do not add a trailing slash or the repository path. If you use a custom domain, use that origin instead.
3. Deploy and copy the backend's public HTTPS URL, e.g. `https://clipnest-downloader.onrender.com`.

## 3. Connect the website to the backend
1. Edit `index.html` in your GitHub repository.
2. Near the bottom, find `const API_BASE = "https://YOUR-CLIPNEST-BACKEND.onrender.com";`.
3. Replace the example URL with your real Render backend URL (no trailing slash), commit the change, and wait for GitHub Pages to redeploy.
4. Open the website and test with a public video you own or are authorized to save.

## Important
- The first request to a free/idle backend may take a little while if the host spins it up. Hosting plans and availability vary.
- Some sites block downloading or prohibit it in their terms. This does not bypass DRM, private content, paywalls, or account permissions. Only download content you own or have permission to save.
- This is a small starter, not a hardened public service. It uses in-memory job state and temporary files, so restarts may interrupt downloads. Add authentication, quotas, rate limits, and persistent job/file storage before inviting the public. Large files may exceed host storage or bandwidth limits.
