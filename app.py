from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash
import stripe
import qrcode
from io import BytesIO, StringIO
import base64
import uuid
import secrets
from flask_mail import Mail, Message
import os
import threading
import json
import ast
import csv
import hashlib
import zipfile
import subprocess
import tempfile
from datetime import datetime, timezone

app = Flask(__name__,
            template_folder='website/templates',
            static_folder='website/static')

base_url = os.getenv('BASE_URL', 'http://10.0.0.199:5000')
tickets_file = os.getenv('TICKETS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'tickets.json'))
admin_key = os.getenv('ADMIN_KEY', 'section2024')
wallet_team_id = os.getenv('WALLET_TEAM_ID', '')
wallet_pass_type_id = os.getenv('WALLET_PASS_TYPE_ID', 'pass.com.thesection.ticket')
wallet_cert_path = os.getenv('WALLET_CERT_PATH', '')
wallet_key_path = os.getenv('WALLET_KEY_PATH', '')
wallet_wwdr_path = os.getenv('WALLET_WWDR_PATH', '')
wallet_enabled = all([wallet_team_id, wallet_cert_path, wallet_key_path, wallet_wwdr_path])
members_file = os.getenv('MEMBERS_FILE', os.path.join(os.path.dirname(__file__), 'data', 'legacy_members.json'))
PLACEHOLDER_TRUNCATED_FOR_SIZE