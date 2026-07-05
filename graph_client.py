import requests
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

class GraphException(Exception):
    pass

def _get_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

def get_rooms(token: str, min_capacity: int = 1) -> List[Dict[str, Any]]:
    """Retrieves all conference rooms and filters them by minimum capacity.
    Follows @odata.nextLink for pagination to support large enterprise environments.
    Endpoint: GET /places/microsoft.graph.room
    """
    url = f"{GRAPH_BASE_URL}/places/microsoft.graph.room"
    headers = _get_headers(token)
    
    try:
        all_rooms = []
        # Paginate through all rooms using @odata.nextLink
        while url:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                raise GraphException(f"Failed to fetch rooms: {response.text}")
            data = response.json()
            all_rooms.extend(data.get("value", []))
            url = data.get("@odata.nextLink")  # None if no more pages
        
        filtered_rooms = []
        for room in all_rooms:
            capacity = room.get("capacity")
            # Rooms without capacity configured are excluded by default
            room_capacity = int(capacity) if capacity is not None else 0
            if room_capacity >= min_capacity:
                filtered_rooms.append({
                    "displayName": room.get("displayName"),
                    "emailAddress": room.get("emailAddress"),
                    "capacity": room_capacity
                })
        return filtered_rooms
    except GraphException:
        raise
    except Exception as e:
        logger.error(f"Error in get_rooms: {e}")
        raise GraphException(f"Error fetching rooms: {e}")

def find_meeting_times(
    token: str,
    attendees: List[str],
    rooms: List[Dict[str, Any]],
    start_time_iso: str,
    end_time_iso: str,
    duration_minutes: int,
    time_zone: str = "Tokyo Standard Time"
) -> List[Dict[str, Any]]:
    """Finds meeting time suggestions considering attendees and rooms.
    Endpoint: POST /me/findMeetingTimes
    """
    url = f"{GRAPH_BASE_URL}/me/findMeetingTimes"
    headers = _get_headers(token)
    
    # Format attendees for payload
    formatted_attendees = [
        {
            "type": "required",
            "emailAddress": {"address": email}
        } for email in attendees
    ]
    
    # Format room locations (max 20 to avoid large payload limits)
    candidate_rooms = rooms[:20]
    formatted_locations = [
        {
            "resolveAvailability": True,
            "displayName": room["displayName"],
            "locationEmailAddress": room["emailAddress"]
        } for room in candidate_rooms
    ]
    
    # Payload Construction
    payload = {
        "attendees": formatted_attendees,
        "locationConstraint": {
            "isRequired": True,
            "suggestLocation": True,
            "locations": formatted_locations
        },
        "timeConstraint": {
            "activityDomain": "work",
            "timeslots": [
                {
                    "start": {
                        "dateTime": start_time_iso,
                        "timeZone": time_zone
                    },
                    "end": {
                        "dateTime": end_time_iso,
                        "timeZone": time_zone
                    }
                }
            ]
        },
        "meetingDuration": f"PT{duration_minutes}M"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            raise GraphException(f"Failed to find meeting times: {response.text}")
            
        data = response.json()
        suggestions = data.get("meetingTimeSuggestions", [])
        
        parsed_suggestions = []
        for sug in suggestions:
            time_slot = sug.get("meetingTimeSlot", {})
            sug_locations = sug.get("locations", [])
            confidence = sug.get("confidence")  # May be None if no confidence value returned
            
            # Build a lookup set of candidate room emails for matching
            candidate_emails = {r["emailAddress"].lower() for r in candidate_rooms}
            
            # Match available rooms: Graph API returns location email in 'uniqueId' or inside the location object
            available_rooms = []
            for loc in sug_locations:
                # locationEmailAddress may be top-level or nested; try both
                email = loc.get("locationEmailAddress") or loc.get("uniqueId", "")
                display = loc.get("displayName", "")
                if email and email.lower() in candidate_emails:
                    available_rooms.append({
                        "displayName": display,
                        "emailAddress": email
                    })
                elif not email:
                    # Fallback: match by displayName if email is absent
                    matched = next(
                        (r for r in candidate_rooms if r["displayName"].lower() == display.lower()),
                        None
                    )
                    if matched:
                        available_rooms.append({
                            "displayName": matched["displayName"],
                            "emailAddress": matched["emailAddress"]
                        })
                    
            if available_rooms:
                parsed_suggestions.append({
                    "start": time_slot.get("start", {}).get("dateTime"),
                    "end": time_slot.get("end", {}).get("dateTime"),
                    "timeZone": time_slot.get("start", {}).get("timeZone"),
                    "confidence": confidence if confidence is not None else 0,
                    "rooms": available_rooms
                })
        return parsed_suggestions
    except GraphException:
        raise
    except Exception as e:
        logger.error(f"Error in find_meeting_times: {e}")
        raise GraphException(f"Error finding meeting times: {e}")

def send_approval_email(
    token: str,
    approver_email: str,
    request_id: str,
    requester_name: str,
    subject: str,
    start_time: str,
    end_time: str,
    room_name: str,
    attendees: List[str],
    app_base_url: str
) -> None:
    """Sends an approval request email to the admin.
    Endpoint: POST /me/sendMail
    """
    url = f"{GRAPH_BASE_URL}/me/sendMail"
    headers = _get_headers(token)
    
    approval_url = f"{app_base_url}?request_id={request_id}"
    
    email_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px; background-color: #fafafa; }}
            .header {{ background-color: #0078d4; color: white; padding: 15px; border-radius: 6px 6px 0 0; text-align: center; }}
            .content {{ padding: 20px; background-color: white; border-radius: 0 0 6px 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
            .details-table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
            .details-table td {{ padding: 8px; border-bottom: 1px solid #f0f0f0; }}
            .details-table td.label {{ font-weight: bold; width: 30%; color: #555555; }}
            .btn-container {{ text-align: center; margin: 25px 0; }}
            .btn {{ display: inline-block; padding: 12px 24px; color: white !important; background-color: #0078d4; text-decoration: none; border-radius: 4px; font-weight: bold; }}
            .footer {{ font-size: 11px; color: #888888; text-align: center; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>Meeting Approval Required</h2>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p><strong>{requester_name}</strong> has requested to book a meeting room. Please review the details below:</p>
                
                <table class="details-table">
                    <tr>
                        <td class="label">Subject</td>
                        <td>{subject}</td>
                    </tr>
                    <tr>
                        <td class="label">Date & Time</td>
                        <td>{start_time} to {end_time} (JST)</td>
                    </tr>
                    <tr>
                        <td class="label">Room</td>
                        <td><strong>{room_name}</strong></td>
                    </tr>
                    <tr>
                        <td class="label">Participants</td>
                        <td>{', '.join(attendees)}</td>
                    </tr>
                </table>
                
                <div class="btn-container">
                    <a href="{approval_url}" class="btn" target="_blank">Review Request & Approve</a>
                </div>
                
                <p class="footer">This is an automated request from the Enterprise Outlook Scheduler.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    payload = {
        "message": {
            "subject": f"Approval Requested: Room Booking ({subject})",
            "body": {
                "contentType": "HTML",
                "content": email_body
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": approver_email
                    }
                }
            ]
        },
        "saveToSentItems": "true"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 202:
            raise GraphException(f"Failed to send email: {response.text}")
    except Exception as e:
        logger.error(f"Error sending approval email: {e}")
        raise GraphException(f"Error sending approval email: {e}")

def create_event(
    token: str,
    subject: str,
    start_time: str,
    end_time: str,
    room_email: str,
    room_name: str,
    attendees: List[str],
    time_zone: str = "Tokyo Standard Time"
) -> Dict[str, Any]:
    """Creates a calendar event and automatically books the room resource with Teams link.
    Endpoint: POST /me/events
    """
    url = f"{GRAPH_BASE_URL}/me/events"
    headers = _get_headers(token)
    
    # Format attendees, including room resource
    formatted_attendees = []
    
    # User attendees
    for email in attendees:
        formatted_attendees.append({
            "emailAddress": {"address": email},
            "type": "required"
        })
        
    # Room resource attendee (critical for Outlook room auto-processing)
    formatted_attendees.append({
        "emailAddress": {
            "address": room_email,
            "name": room_name
        },
        "type": "resource"
    })
    
    payload = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content": f"Meeting room booking: {room_name}. Generated by Enterprise Scheduler."
        },
        "start": {
            "dateTime": start_time,
            "timeZone": time_zone
        },
        "end": {
            "dateTime": end_time,
            "timeZone": time_zone
        },
        "location": {
            "displayName": room_name,
            "locationEmailAddress": room_email
        },
        "attendees": formatted_attendees,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 201:
            raise GraphException(f"Failed to create event: {response.text}")
        return response.json()
    except Exception as e:
        logger.error(f"Error in create_event: {e}")
        raise GraphException(f"Error creating event: {e}")

def send_rejection_email(
    token: str,
    requester_email: str,
    subject: str,
    start_time: str,
    room_name: str
) -> None:
    """Sends a notification to the requester indicating their booking was rejected.
    Endpoint: POST /me/sendMail
    """
    url = f"{GRAPH_BASE_URL}/me/sendMail"
    headers = _get_headers(token)
    
    email_body = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; color: #333;">
        <h3 style="color: #dc3545;">Meeting Room Request Rejected</h3>
        <p>Your request to book the room <strong>{room_name}</strong> for the meeting <strong>"{subject}"</strong> on <strong>{start_time}</strong> has been <strong>rejected</strong> by the administrator.</p>
        <p>Please select another time or room and submit a new request through the scheduler.</p>
    </body>
    </html>
    """
    
    payload = {
        "message": {
            "subject": f"Rejected: Room Booking Request ({subject})",
            "body": {
                "contentType": "HTML",
                "content": email_body
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": requester_email
                    }
                }
            ]
        },
        "saveToSentItems": "true"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 202:
            raise GraphException(f"Failed to send rejection email: {response.text}")
    except GraphException:
        raise
    except Exception as e:
        logger.error(f"Error sending rejection email: {e}")
        raise GraphException(f"Error sending rejection email: {e}")


def send_approval_confirmation_email(
    token: str,
    requester_email: str,
    requester_name: str,
    subject: str,
    start_time: str,
    end_time: str,
    room_name: str,
    attendees: List[str]
) -> None:
    """Sends a confirmation notification to the requester when their booking is approved.
    Called after create_event has completed successfully.
    Endpoint: POST /me/sendMail
    """
    url = f"{GRAPH_BASE_URL}/me/sendMail"
    headers = _get_headers(token)
    
    email_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px; background-color: #fafafa; }}
            .header {{ background-color: #28a745; color: white; padding: 15px; border-radius: 6px 6px 0 0; text-align: center; }}
            .content {{ padding: 20px; background-color: white; border-radius: 0 0 6px 6px; }}
            .details-table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
            .details-table td {{ padding: 8px; border-bottom: 1px solid #f0f0f0; }}
            .details-table td.label {{ font-weight: bold; width: 30%; color: #555; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h2>Meeting Room Booking Confirmed ✅</h2>
            </div>
            <div class="content">
                <p>Dear {requester_name},</p>
                <p>Your meeting room booking has been <strong>approved and added to your Outlook calendar</strong>.</p>
                
                <table class="details-table">
                    <tr><td class="label">Subject</td><td>{subject}</td></tr>
                    <tr><td class="label">Date &amp; Time</td><td>{start_time} to {end_time} (JST)</td></tr>
                    <tr><td class="label">Room</td><td><strong>{room_name}</strong></td></tr>
                    <tr><td class="label">Participants</td><td>{', '.join(attendees)}</td></tr>
                </table>
                
                <p>A Teams meeting invitation has been automatically generated and sent to all participants.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    payload = {
        "message": {
            "subject": f"Confirmed: Room Booking ({subject})",
            "body": {
                "contentType": "HTML",
                "content": email_body
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": requester_email
                    }
                }
            ]
        },
        "saveToSentItems": "true"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 202:
            logger.warning(f"Approval confirmation email not sent (status {response.status_code}): {response.text}")
    except Exception as e:
        # Non-fatal: log and continue even if confirmation email fails
        logger.error(f"Error sending approval confirmation email: {e}")

