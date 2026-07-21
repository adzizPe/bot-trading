# Deployment Policy

- Project ini hanya menggunakan deployment native di VPS.
- Jangan menambahkan teknologi packaging atau virtualisasi deployment kecuali pengguna memintanya secara eksplisit.
- Backend dijalankan langsung dari Python virtual environment dengan FastAPI/Uvicorn.
- Frontend di-build dengan Vite dan hasil `frontend/dist` dilayani oleh Nginx.
- Nginx menjadi reverse proxy untuk API dan WebSocket.
- Gunakan NSSM pada Windows VPS atau PM2 jika sesuai untuk process management.
- Dokumentasi setup, update, backup, rollback, dan recovery harus memakai alur native tersebut.
