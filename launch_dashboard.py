"""
Local HTTP Server Launcher for Quant Club Sector Index Terminal
================================================================
Serves dashboard.html at http://localhost:8000/dashboard.html and automatically opens the browser!
"""

import http.server
import socketserver
import webbrowser
import os

PORT = 8000
BASE_DIR = r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management"

os.chdir(BASE_DIR)

Handler = http.server.SimpleHTTPRequestHandler

print("="*70)
print(f"STARTING LOCAL QUANT CLUB SECTOR INDEX DASHBOARD ON http://localhost:{PORT}")
print("Press Ctrl+C to stop server.")
print("="*70)

webbrowser.open(f"http://localhost:{PORT}/dashboard.html")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard Server stopped cleanly.")
