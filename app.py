import streamlit as st
import datetime
import uuid
import os
import logging
from dotenv import load_dotenv

# Load local environment variables
load_dotenv()

from db_client import DBClient
import auth
import graph_client

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB Client
db_client = DBClient()

# Page config
st.set_page_config(
    page_title="Enterprise Outlook Scheduler",
    page_icon="📅",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for rich aesthetics (gradient headers, glassmorphism, nice badges)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Outfit', -apple-system, BlinkMacSystemFont, sans-serif;
}

.main-title {
    font-size: 2.8rem;
    font-weight: 700;
    margin-bottom: 5px;
}

.gradient-text {
    background: linear-gradient(90deg, #0078d4, #00c6ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.subtitle {
    color: #888;
    font-size: 1.1rem;
    margin-bottom: 30px;
}

.glass-card {
    background: rgba(255, 255, 255, 0.03);
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 24px;
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.2);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    margin-bottom: 20px;
}

.status-badge {
    padding: 6px 12px;
    border-radius: 20px;
    font-weight: bold;
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 1px;
    display: inline-block;
}

.status-pending {
    background-color: rgba(255, 193, 7, 0.15);
    color: #ffc107;
    border: 1px solid rgba(255, 193, 7, 0.3);
}

.status-approved {
    background-color: rgba(40, 167, 69, 0.15);
    color: #28a745;
    border: 1px solid rgba(40, 167, 69, 0.3);
}

.status-rejected {
    background-color: rgba(220, 53, 69, 0.15);
    color: #dc3545;
    border: 1px solid rgba(220, 53, 69, 0.3);
}

.sidebar-profile {
    padding: 15px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.05);
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

def render_login_screen():
    st.markdown("""
    <div style="text-align: center; margin-top: 80px;">
        <h1 class="main-title">Enterprise <span class='gradient-text'>Outlook Scheduler</span></h1>
        <p class="subtitle">Secure, stateless meeting scheduling and room booking with HITL approval.</p>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("<div class='glass-card' style='text-align: center;'>", unsafe_allow_html=True)
        st.markdown("<h4>M365 Sign In Required</h4>", unsafe_allow_html=True)
        st.write("Please sign in with your organization's Microsoft 365 account to verify identity and access calendars.")
        
        redirect_uri = os.environ.get("REDIRECT_URI", "http://localhost:8501")
        
        try:
            auth_uri = auth.initiate_auth_flow(db_client, redirect_uri)
            st.markdown(f"""
            <div style="margin-top: 25px;">
                <a href="{auth_uri}" target="_self" style="text-decoration: none;">
                    <span style="background-color: #0078d4; color: white; padding: 12px 30px; border-radius: 6px; font-weight: bold; cursor: pointer; display: inline-block; box-shadow: 0 4px 15px rgba(0, 120, 212, 0.4); transition: all 0.3s ease;">
                        Sign In with Microsoft
                    </span>
                </a>
            </div>
            """, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Failed to initiate login flow: {e}")
            
        st.markdown("</div>", unsafe_allow_html=True)

def submit_booking_request(slot: dict, selected_room: dict):
    subject = st.session_state.search_params["subject"]
    attendees = st.session_state.search_params["attendees"]
    
    request_id = str(uuid.uuid4())
    
    request_data = {
        "subject": subject,
        "start_time": slot["start"],
        "end_time": slot["end"],
        "attendees": attendees,
        "room_email": selected_room["emailAddress"],
        "room_name": selected_room["displayName"],
        "requester_id": st.session_state.home_account_id,
        "requester_email": st.session_state.user_email,
        "requester_name": st.session_state.user_name,
        "status": "pending"
    }
    
    try:
        # 1. Save pending request in database
        db_client.save_booking_request(request_id, request_data)
        
        # 2. Get requester token silently to send the email
        token = auth.get_token_silently(db_client, st.session_state.home_account_id)
        
        # 3. Determine the base application URL (strip callback if present)
        redirect_uri = os.environ.get("REDIRECT_URI", "http://localhost:8501")
        app_base_url = redirect_uri.replace('/callback', '')
        
        # 4. Dispatch the approval request email
        approver_email = os.environ.get("APPROVER_EMAIL")
        if not approver_email:
            raise Exception("APPROVER_EMAIL environment variable is not configured.")
            
        graph_client.send_approval_email(
            token=token,
            approver_email=approver_email,
            request_id=request_id,
            requester_name=st.session_state.user_name,
            subject=subject,
            start_time=slot["start"].replace("T", " "),
            end_time=slot["end"].replace("T", " "),
            room_name=selected_room["displayName"],
            attendees=attendees,
            app_base_url=app_base_url
        )
        
        st.success(f"Request submitted! Approval email sent to {approver_email}.")
        # Clear search results to reset form
        st.session_state.search_results = None
        st.rerun()
    except Exception as e:
        st.error(f"Failed to submit booking request: {e}")

def render_scheduler_screen():
    # Sidebar
    with st.sidebar:
        st.markdown(f"""
        <div class="sidebar-profile">
            <h5 style="margin: 0; color: #fff;">Signed In As</h5>
            <p style="margin: 0; font-size: 14px; font-weight: bold; color: #55afff;">{st.session_state.user_name}</p>
            <p style="margin: 0; font-size: 12px; color: #888;">{st.session_state.user_email}</p>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("Sign Out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    # Main Workspace
    st.markdown("<h1>📅 M365 <span class='gradient-text'>Scheduler Dashboard</span></h1>", unsafe_allow_html=True)
    st.write("Configure meeting details, search available time slots, and submit booking requests for approval.")
    
    # Input Form inside a container
    with st.container():
        st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
        st.markdown("<h4>1. Meeting Details</h4>", unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        with col1:
            subject = st.text_input("Meeting Subject", placeholder="e.g. Weekly Design Alignment")
            attendees_input = st.text_area(
                "Required Participants (comma-separated emails)", 
                value=st.session_state.user_email,
                help="Include your email and other participants' emails."
            )
            
        with col2:
            date_input = st.date_input("Preferred Date", datetime.date.today() + datetime.timedelta(days=1))
            
            # Configure search window times
            time_options = [datetime.time(hour, minute) for hour in range(7, 21) for minute in (0, 30)]
            time_labels = [t.strftime("%H:%M") for t in time_options]
            
            col_start, col_end = st.columns(2)
            with col_start:
                start_time_str = st.selectbox("Search Window Start", time_labels, index=time_labels.index("09:00"))
            with col_end:
                end_time_str = st.selectbox("Search Window End", time_labels, index=time_labels.index("18:00"))
                
            col_dur, col_cap = st.columns(2)
            with col_dur:
                duration = st.selectbox("Duration", [30, 45, 60, 90, 120], index=2, format_func=lambda x: f"{x} mins")
            with col_cap:
                min_capacity = st.number_input("Minimum Room Capacity", min_value=1, value=2, step=1)
                
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Search Trigger
        if st.button("Search Available Slots & Rooms", type="primary", use_container_width=True):
            attendee_emails = [email.strip() for email in attendees_input.split(",") if email.strip()]
            if not subject:
                st.error("Please enter a meeting subject.")
            elif not attendee_emails:
                st.error("Please include at least one attendee email.")
            else:
                start_time = datetime.datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.datetime.strptime(end_time_str, "%H:%M").time()
                
                if start_time >= end_time:
                    st.error("Search window start must be before end time.")
                else:
                    with st.spinner("Finding available timeslots and rooms in Exchange..."):
                        try:
                            # 1. Silent token retrieve
                            token = auth.get_token_silently(db_client, st.session_state.home_account_id)
                            
                            # 2. Get rooms filtering by capacity
                            rooms = graph_client.get_rooms(token, min_capacity)
                            
                            if not rooms:
                                st.warning(f"No conference rooms matching a capacity of {min_capacity} or higher were found.")
                                st.session_state.search_results = None
                            else:
                                # 3. Convert input elements to ISO string format
                                start_iso = f"{date_input.isoformat()}T{start_time.strftime('%H:%M:%S')}"
                                end_iso = f"{date_input.isoformat()}T{end_time.strftime('%H:%M:%S')}"
                                
                                # 4. Call MS Graph API
                                suggestions = graph_client.find_meeting_times(
                                    token=token,
                                    attendees=attendee_emails,
                                    rooms=rooms,
                                    start_time_iso=start_iso,
                                    end_time_iso=end_iso,
                                    duration_minutes=duration
                                )
                                
                                st.session_state.search_results = suggestions
                                st.session_state.search_params = {
                                    "subject": subject,
                                    "attendees": attendee_emails,
                                    "date": date_input
                                }
                                
                                if not suggestions:
                                    st.warning("No mutually available time slots found. Adjust parameters and search again.")
                        except auth.TokenExpiredException:
                            st.error("M365 credentials expired. Please log in again.")
                            st.session_state.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Search failed: {e}")

    # Display Search Results
    if st.session_state.get("search_results"):
        st.markdown("<h3 style='margin-top: 30px;'>💡 Suggested Availability & Rooms</h3>", unsafe_allow_html=True)
        st.write("Select a timeslot, pick an available room, and submit for admin approval.")
        
        for idx, slot in enumerate(st.session_state.search_results):
            # Parse datetime representations cleanly for display
            start_t = slot["start"].split("T")[1][:5]
            end_t = slot["end"].split("T")[1][:5]
            confidence = slot["confidence"]
            
            with st.container():
                st.markdown(f"""
                <div class="glass-card" style="padding: 18px; margin-bottom: 15px; border-left: 5px solid #0078d4;">
                    <span style="font-size: 1.25rem; font-weight: 600;">⏰ {start_t} - {end_t}</span>
                    <span style="color: #aaa; margin-left: 15px; font-size: 14px;">(Match Confidence: {confidence}%)</span>
                </div>
                """, unsafe_allow_html=True)
                
                col_select, col_submit = st.columns([3, 1])
                room_list = slot["rooms"]
                room_options = [f"{r['displayName']} ({r['emailAddress']})" for r in room_list]
                
                with col_select:
                    selected_room_str = st.selectbox(
                        f"Available Rooms", 
                        options=room_options, 
                        key=f"room_sel_{idx}",
                        label_visibility="collapsed"
                    )
                with col_submit:
                    if st.button("Request Booking", key=f"req_btn_{idx}", type="primary", use_container_width=True):
                        # Locate index of selected option
                        room_idx = room_options.index(selected_room_str)
                        chosen_room = room_list[room_idx]
                        submit_booking_request(slot, chosen_room)

def render_approval_mode(request_id: str):
    st.markdown("<h1>📅 Booking Approval <span class='gradient-text'>Console</span></h1>", unsafe_allow_html=True)
    st.write("Analyze and process the pending Outlook meeting room reservation.")

    # --- SECURITY CHECK 1: Approver must be authenticated ---
    if "home_account_id" not in st.session_state:
        # Preserve the request_id so we can return here after OAuth completes
        st.session_state.pending_approval_request_id = request_id
        st.markdown("""
        <div class='glass-card' style='text-align:center; max-width:480px; margin:80px auto;'>
            <h3>🔐 Approver Authentication Required</h3>
            <p style='color:#aaa;'>Please sign in with your M365 account to access the approval console.</p>
        </div>
        """, unsafe_allow_html=True)
        redirect_uri = os.environ.get("REDIRECT_URI", "http://localhost:8501")
        try:
            auth_uri = auth.initiate_auth_flow(db_client, redirect_uri)
            st.markdown(f"""
            <div style='text-align:center; margin-top:15px;'>
                <a href="{auth_uri}" target="_self" style="text-decoration:none;">
                    <span style="background-color:#0078d4;color:white;padding:12px 30px;border-radius:6px;font-weight:bold;display:inline-block;">
                        Sign In with Microsoft
                    </span>
                </a>
            </div>
            """, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Failed to initiate login: {e}")
        return

    # --- SECURITY CHECK 2: Verify the logged-in user is an authorized approver ---
    raw_approver_env = os.environ.get("APPROVER_EMAIL", "")
    authorized_approvers = [e.strip().lower() for e in raw_approver_env.split(",") if e.strip()]
    logged_in_email = (st.session_state.get("user_email") or "").lower()

    if not authorized_approvers:
        st.error("⚠️ APPROVER_EMAIL is not configured. Please set it in the environment variables.")
        return

    if logged_in_email not in authorized_approvers:
        st.error(f"🚫 Access denied. Your account ({st.session_state.get('user_email')}) is not authorized to approve requests.")
        st.info("Please contact the system administrator if you believe this is an error.")
        if st.button("Sign out and try a different account"):
            st.session_state.clear()
            st.rerun()
        return

    # Retrieve request
    request = db_client.get_booking_request(request_id)
    if not request:
        st.error(f"Meeting reservation request with ID '{request_id}' could not be resolved.")
        if st.button("Navigate to Scheduler"):
            st.query_params.clear()
            st.rerun()
        return

    # --- SECURITY CHECK 3: Check if approval link has expired ---
    expires_at_str = request.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
            if datetime.datetime.now(datetime.timezone.utc) > expires_at:
                st.error("⏰ This approval link has expired (valid for 7 days from submission).")
                st.info("Please ask the requester to submit a new booking request.")
                db_client.update_booking_request_status(request_id, "expired")
                return
        except Exception as e:
            logger.warning(f"Could not parse expires_at '{expires_at_str}': {e}")
    status = request.get("status", "pending")
    status_class = "status-pending"
    if status == "approved":
        status_class = "status-approved"
    elif status == "rejected":
        status_class = "status-rejected"

    st.markdown(f"""
    <div class="glass-card">
        <h3 style="margin-top: 0;">{request['subject']}</h3>
        <p><strong>Status:</strong> <span class="status-badge {status_class}">{status}</span></p>
        <p style="font-size:12px; color:#888;">Reviewing as: <strong>{st.session_state.get('user_name')} ({st.session_state.get('user_email')})</strong></p>
        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.08); margin: 18px 0;">
        <table style="width: 100%; font-size: 15px;">
            <tr style="height: 35px;">
                <td style="color: #888; font-weight: bold; width: 25%;">Requested Date/Time</td>
                <td>{request['start_time'].replace('T', ' ')} to {request['end_time'].replace('T', ' ')}</td>
            </tr>
            <tr style="height: 35px;">
                <td style="color: #888; font-weight: bold;">Conference Room</td>
                <td><strong>{request['room_name']}</strong> ({request['room_email']})</td>
            </tr>
            <tr style="height: 35px;">
                <td style="color: #888; font-weight: bold;">Organizer/Applicant</td>
                <td>{request['requester_name']} ({request['requester_email']})</td>
            </tr>
            <tr style="height: 35px;">
                <td style="color: #888; font-weight: bold;">Required Participants</td>
                <td>{', '.join(request['attendees'])}</td>
            </tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

    if status == "pending":
        col_approve, col_reject = st.columns(2)
        with col_approve:
            if st.button("Approve & Book Room", type="primary", use_container_width=True):
                with st.spinner("Acquiring requester credentials and sending event..."):
                    try:
                        # 1. Fetch user's token silently
                        user_token = auth.get_token_silently(db_client, request['requester_id'])
                        
                        # 2. Book event on behalf of requester (Gemini.md §5.3.3)
                        graph_client.create_event(
                            token=user_token,
                            subject=request['subject'],
                            start_time=request['start_time'],
                            end_time=request['end_time'],
                            room_email=request['room_email'],
                            room_name=request['room_name'],
                            attendees=request['attendees']
                        )
                        
                        # 3. Update database status
                        db_client.update_booking_request_status(
                            request_id=request_id,
                            status="approved",
                            approver_email=os.environ.get("APPROVER_EMAIL", "admin")
                        )
                        
                        # 4. Send approval confirmation email to requester (Gemini.md §5.3.3)
                        try:
                            graph_client.send_approval_confirmation_email(
                                token=user_token,
                                requester_email=request['requester_email'],
                                requester_name=request['requester_name'],
                                subject=request['subject'],
                                start_time=request['start_time'].replace('T', ' '),
                                end_time=request['end_time'].replace('T', ' '),
                                room_name=request['room_name'],
                                attendees=request['attendees']
                            )
                        except Exception as mail_err:
                            logger.warning(f"Approval confirmation email could not be sent: {mail_err}")
                        
                        st.success("Successfully approved! Calendar event created on behalf of the requester. Confirmation email sent.")
                        st.rerun()
                    except auth.TokenExpiredException:
                        st.error("Requester session has expired and cannot be refreshed silently. Ask requester to sign in again.")
                    except Exception as e:
                        st.error(f"Approval failed: {e}")
                        
        with col_reject:
            if st.button("Reject Request", use_container_width=True):
                with st.spinner("Processing rejection..."):
                    try:
                        db_client.update_booking_request_status(
                            request_id=request_id,
                            status="rejected",
                            approver_email=os.environ.get("APPROVER_EMAIL", "admin")
                        )
                        
                        # Try to send email using requester token
                        try:
                            user_token = auth.get_token_silently(db_client, request['requester_id'])
                            graph_client.send_rejection_email(
                                token=user_token,
                                requester_email=request['requester_email'],
                                subject=request['subject'],
                                start_time=request['start_time'].replace('T', ' '),
                                room_name=request['room_name']
                            )
                        except Exception as e:
                            logger.error(f"Could not dispatch rejection notification via Graph: {e}")
                            
                        st.success("Request rejected. Notification sent to requester.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Rejection failed: {e}")
    else:
        st.info("This request has already been processed.")
        if st.button("Navigate to Scheduler"):
            st.query_params.clear()
            st.rerun()

# -----------------------------
# MAIN APP ROUTING
# -----------------------------
request_id = st.query_params.get("request_id")

if request_id:
    render_approval_mode(request_id)
else:
    # Check for OAuth callback parameters
    code = st.query_params.get("code")
    state = st.query_params.get("state")
    
    if code and state:
        # OAuth Redirect callback processing
        with st.spinner("Completing M365 authentication..."):
            try:
                redirect_uri = os.environ.get("REDIRECT_URI", "http://localhost:8501")
                account = auth.complete_auth_flow(db_client, st.query_params, redirect_uri)
                
                st.session_state.home_account_id = account["home_account_id"]
                st.session_state.user_email = account.get("username")
                st.session_state.user_name = account.get("name", account.get("username"))
                
                st.query_params.clear()
                
                # If the user was redirected here from the approval page, send them back
                pending_request_id = st.session_state.pop("pending_approval_request_id", None)
                if pending_request_id:
                    st.query_params["request_id"] = pending_request_id
                
                st.rerun()
            except Exception as e:
                st.error(f"Authentication completed with errors: {e}")
                if st.button("Retry Sign In"):
                    st.query_params.clear()
                    st.rerun()
    else:
        # Standard workflow
        if "home_account_id" not in st.session_state:
            render_login_screen()
        else:
            render_scheduler_screen()
