import re

import requests
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime
from sqlalchemy import or_

from app import db
from forms import RouteForm
from models import Route, RouteStatus, User, UserRole, Driver, Task, TaskStatus
from services import RouteService
from utils import role_required, company_access_required, log_action, extract_coordinates_from_maps_url
from models import ActionType
import json

routes = Blueprint('routes', __name__, url_prefix='/routes')


@routes.route('/')
@login_required
def list_routes():
    """
    List routes with filtering options
    """
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', None)
    search_term = request.args.get('search', '')
    driver_id = request.args.get('driver_id', None, type=int)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Admin can see all routes if no company filter
    if current_user.role == UserRole.ADMIN and not company_id:
        company_id = request.args.get('company_id', None, type=int)

    # For drivers, only show their routes
    if current_user.role == UserRole.DRIVER:
        driver_id = current_user.driver.id

    # Build query
    routes_query = Route.query

    # Apply filters
    if company_id:
        routes_query = routes_query.filter(Route.company_id == company_id)

    if driver_id:
        routes_query = routes_query.filter(Route.driver_id == driver_id)

    # Convert status string to enum if provided
    route_status = None
    if status:
        try:
            route_status = RouteStatus(status)
            routes_query = routes_query.filter(Route.status == route_status)
        except ValueError:
            # Invalid status value, ignore filter
            pass

    # Apply search if provided
    if search_term:
        search = f"%{search_term}%"
        routes_query = routes_query.filter(
            or_(
                Route.start_point.ilike(search),
                Route.end_point.ilike(search)
            )
        )

    # Order by creation date (newest first)
    routes_query = routes_query.order_by(Route.start_time.desc())

    # Paginate results
    routes = routes_query.paginate(page=page, per_page=10)

    # Get all available statuses
    statuses = [status.value for status in RouteStatus]

    # Get all drivers for filter dropdown (for admin, company owner, manager)
    drivers = []
    driver_name = None
    if current_user.role in [UserRole.ADMIN, UserRole.COMPANY_OWNER, UserRole.MANAGER, UserRole.OPERATOR]:
        if company_id:
            drivers = Driver.query.filter_by(company_id=company_id).all()
        else:
            drivers = Driver.query.all()

        if driver_id:
            driver = Driver.query.get(driver_id)
            if driver and driver.user:
                driver_name = f"{driver.user.first_name} {driver.user.last_name}"

    log_action(ActionType.VIEW, "Viewed routes list", db)

    return render_template(
        'routes/list_routes.html',
        title='Routes',
        routes=routes,
        search_term=search_term,
        current_status=status,
        statuses=statuses,
        drivers=drivers,
        driver_name=driver_name
    )


@routes.route('/create', methods=['GET', 'POST'])
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER", "OPERATOR"])
def create_route():
    """
    Create a new route
    """
    form = RouteForm()

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.ADMIN:
        # Admin must select a company
        company_id = request.args.get('company_id', None, type=int)

    # If no company ID, redirect to dashboard
    if not company_id and current_user.role != UserRole.ADMIN:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Get task ID if provided
    task_id = request.args.get('task_id', None, type=int)
    if task_id:
        task = Task.query.get_or_404(task_id)
        if form.task_id:
            form.task_id.data = task_id

        # Pre-fill form if task exists
        form.start_point.data = task.start_point if hasattr(task, 'start_point') else ""
        form.end_point.data = task.end_point if hasattr(task, 'end_point') else ""

    # Get available drivers for this company
    drivers = []
    if company_id:
        driver_query = Driver.query.filter_by(company_id=company_id)
        drivers = [(d.id, f"{d.user.first_name} {d.user.last_name}") for d in driver_query.all()]

    # Add empty option
    drivers.insert(0, (0, 'Select a driver'))
    form.driver_id.choices = drivers

    # If task_id provided, get tasks for dropdown
    if hasattr(form, 'task_id') and not task_id and company_id:
        tasks = Task.query.filter_by(company_id=company_id).filter(
            Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS])
        ).all()
        form.task_id.choices = [(t.id, t.title) for t in tasks]
        form.task_id.choices.insert(0, (0, 'No task'))

    if form.validate_on_submit():
        try:
            # Parse waypoints if provided
            waypoints = None
            if form.waypoints.data:
                try:
                    waypoints = json.loads(form.waypoints.data)
                except json.JSONDecodeError:
                    # If not valid JSON, keep as None
                    pass

            # Convert task_id=0 to None (Fix for foreign key constraint)
            processed_task_id = None
            if hasattr(form, 'task_id') and form.task_id.data and form.task_id.data != 0:
                processed_task_id = form.task_id.data

            # Create route directly instead of using RouteService
            route = Route(
                start_point=form.start_point.data,
                end_point=form.end_point.data,
                distance=form.distance.data,
                estimated_time=form.estimated_time.data,
                start_time=form.start_time.data,
                status=RouteStatus.PLANNED,
                driver_id=form.driver_id.data,
                task_id=processed_task_id,  # Using processed task_id instead of the raw form value
                company_id=company_id,
                waypoints=waypoints,
            )

            db.session.add(route)

            # Update task status if task is assigned
            if processed_task_id:
                task = Task.query.get(processed_task_id)
                if task and task.status == TaskStatus.NEW:
                    task.status = TaskStatus.IN_PROGRESS

            db.session.commit()
            log_action(ActionType.CREATE, f"Created route from {route.start_point} to {route.end_point}", db)

            flash(f'Route from {route.start_point} to {route.end_point} created successfully!', 'success')
            return redirect(url_for('routes.view_route', route_id=route.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating route: {str(e)}', 'danger')

    return render_template(
        'routes/create_route.html',
        title='Create Route',
        form=form
    )


@routes.route('/<int:route_id>')
@login_required
def view_route(route_id):
    """
    View route details
    """
    route = Route.query.get_or_404(route_id)

    # Check if user has access to this route
    if not _can_access_route(route):
        flash('You do not have permission to access this route.', 'danger')
        return redirect(url_for('routes.list_routes'))

    # Parse waypoints and add status
    waypoint_progress = {"total": 0, "completed": 0, "percentage": 0}
    if route.waypoints:
        waypoint_progress["total"] = len(route.waypoints)
        waypoint_progress["completed"] = sum(1 for w in route.waypoints if w.get('completed', False))
        if waypoint_progress["total"] > 0:
            waypoint_progress["percentage"] = int(waypoint_progress["completed"] / waypoint_progress["total"] * 100)

    log_action(ActionType.VIEW, f"Viewed route {route.id}", db)

    return render_template(
        'routes/view_route.html',
        title=f'Route: {route.start_point} to {route.end_point}',
        route=route,
        waypoint_progress=waypoint_progress
    )


@routes.route('/<int:route_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_route(route_id):
    """
    Edit an existing route
    """
    route = Route.query.get_or_404(route_id)

    # Check if user has access to edit this route
    if not _can_edit_route(route):
        flash('You do not have permission to edit this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    form = RouteForm()

    # Get available drivers for this company
    drivers = []
    if route.company_id:
        driver_query = Driver.query.filter_by(company_id=route.company_id)
        drivers = [(d.id, f"{d.user.first_name} {d.user.last_name}") for d in driver_query.all()]

    # Add empty option and set form choices
    drivers.insert(0, (0, 'Select a driver'))
    form.driver_id.choices = drivers

    if request.method == 'GET':
        # Fill form with route data
        form.start_point.data = route.start_point
        form.end_point.data = route.end_point
        form.distance.data = route.distance
        form.estimated_time.data = route.estimated_time
        form.start_time.data = route.start_time
        form.status.data = route.status.value
        form.driver_id.data = route.driver_id

        # Format waypoints as JSON string
        if route.waypoints:
            form.waypoints.data = json.dumps(route.waypoints)

    if form.validate_on_submit():
        try:
            # Parse waypoints if provided
            waypoints = None
            if form.waypoints.data:
                try:
                    waypoints = json.loads(form.waypoints.data)
                except json.JSONDecodeError:
                    # If not valid JSON, keep as None
                    pass

            # Update route details
            route.start_point = form.start_point.data
            route.end_point = form.end_point.data
            route.distance = form.distance.data
            route.estimated_time = form.estimated_time.data
            route.start_time = form.start_time.data
            route.status = RouteStatus(form.status.data)
            route.driver_id = form.driver_id.data

            # Update waypoints if provided
            if waypoints is not None:
                route.waypoints = waypoints

            # If route status changed to completed, update related task
            if form.status.data == RouteStatus.COMPLETED.value and route.task and route.task.status != TaskStatus.COMPLETED:
                route.task.status = TaskStatus.COMPLETED

                # Also set end time if not already set
                if not route.end_time:
                    route.end_time = datetime.utcnow()

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated route from {route.start_point} to {route.end_point}", db)

            flash('Route updated successfully!', 'success')
            return redirect(url_for('routes.view_route', route_id=route.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating route: {str(e)}', 'danger')

    return render_template(
        'routes/edit_route.html',
        title=f'Edit Route',
        form=form,
        route=route
    )


@routes.route('/<int:route_id>/delete', methods=['POST'])
@login_required
def delete_route(route_id):
    """
    Delete a route
    """
    route = Route.query.get_or_404(route_id)

    # Check if user has access to delete this route
    if not _can_delete_route(route):
        flash('You do not have permission to delete this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # Store route info for log
        route_info = f"{route.start_point} to {route.end_point}"

        # Update task status if needed
        if route.task and route.task.status == TaskStatus.IN_PROGRESS:
            route.task.status = TaskStatus.NEW

        # Delete route
        db.session.delete(route)
        db.session.commit()

        log_action(ActionType.DELETE, f"Deleted route {route_info}", db)
        flash(f'Route from {route_info} deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting route: {str(e)}', 'danger')

    return redirect(url_for('routes.list_routes'))


@routes.route('/geocode-address', methods=['POST'])
@login_required
def geocode_address():
    """
    API endpoint to geocode an address
    """
    address = request.json.get('address', '')

    if not address:
        return jsonify({'success': False, 'error': 'No address provided'})

    try:
        import requests

        encoded_address = address.replace(' ', '+')
        response = requests.get(
            f"https://nominatim.openstreetmap.org/search?format=json&q={encoded_address}&limit=1",
            headers={'User-Agent': 'Logistics CRM'}
        )

        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                return jsonify({
                    'success': True,
                    'lat': float(data[0]['lat']),
                    'lng': float(data[0]['lon']),
                    'display_name': data[0]['display_name']
                })
            else:
                return jsonify({'success': False, 'error': 'Location not found'})
        else:
            return jsonify({'success': False, 'error': f'Geocoding API error: {response.status_code}'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@routes.route('/<int:route_id>/start', methods=['POST'])
@login_required
def start_route(route_id):
    """
    Mark a route as started (in progress)
    """
    route = Route.query.get_or_404(route_id)

    # Only driver assigned to this route can start it
    if current_user.role != UserRole.DRIVER or not current_user.driver or current_user.driver.id != route.driver_id:
        flash('You do not have permission to start this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    # Route must be in 'planned' status
    if route.status != RouteStatus.PLANNED:
        flash('This route cannot be started as it is not in planned status.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # Update route status
        route.status = RouteStatus.IN_PROGRESS
        route.actual_start_time = datetime.utcnow()

        # Update related task if exists
        if route.task and route.task.status != TaskStatus.COMPLETED:
            route.task.status = TaskStatus.IN_PROGRESS

        db.session.commit()
        log_action(ActionType.UPDATE, f"Started route {route.id}", db)
        flash('Route started successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error starting route: {str(e)}', 'danger')

    return redirect(url_for('routes.view_route', route_id=route.id))


@routes.route('/<int:route_id>/complete', methods=['POST'])
@login_required
def complete_route(route_id):
    """
    Mark a route as completed
    """
    route = Route.query.get_or_404(route_id)

    # Only driver assigned to this route can complete it
    if current_user.role != UserRole.DRIVER or not current_user.driver or current_user.driver.id != route.driver_id:
        flash('You do not have permission to complete this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    # Route must be in 'in_progress' status
    if route.status != RouteStatus.IN_PROGRESS:
        flash('This route cannot be completed as it is not in progress.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # Update route status
        route.status = RouteStatus.COMPLETED
        route.end_time = datetime.utcnow()

        # Update related task if exists
        if route.task and route.task.status != TaskStatus.COMPLETED:
            route.task.status = TaskStatus.COMPLETED

        db.session.commit()
        log_action(ActionType.UPDATE, f"Completed route {route.id}", db)
        flash('Route completed successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error completing route: {str(e)}', 'danger')

    return redirect(url_for('routes.view_route', route_id=route.id))


@routes.route('/<int:route_id>/cancel', methods=['POST'])
@login_required
def cancel_route(route_id):
    """
    Cancel a route
    """
    route = Route.query.get_or_404(route_id)

    # Check permissions
    if not _can_cancel_route(route):
        flash('You do not have permission to cancel this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    # Route must not be completed
    if route.status == RouteStatus.COMPLETED:
        flash('This route cannot be cancelled as it is already completed.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # Update route status
        route.status = RouteStatus.CANCELLED

        # Update related task if exists and was in progress
        if route.task and route.task.status == TaskStatus.IN_PROGRESS:
            route.task.status = TaskStatus.NEW

        db.session.commit()
        log_action(ActionType.UPDATE, f"Cancelled route {route.id}", db)
        flash('Route cancelled successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error cancelling route: {str(e)}', 'danger')

    return redirect(url_for('routes.view_route', route_id=route.id))


@routes.route('/<int:route_id>/map')
@login_required
def route_map(route_id):
    """
    Show route on map
    """
    route = Route.query.get_or_404(route_id)

    # Check if user has access to this route
    if not _can_access_route(route):
        flash('You do not have permission to access this route.', 'danger')
        return redirect(url_for('routes.list_routes'))

    # In a real application, process waypoints and geo data
    # Here we'll just pass the raw data to the template

    log_action(ActionType.VIEW, f"Viewed map for route {route.id}", db)

    return render_template(
        'routes/map.html',
        title=f'Route Map: {route.start_point} to {route.end_point}',
        route=route,
        waypoint_progress={"total": len(route.waypoints) if route.waypoints else 0,
                           "completed": sum(
                               1 for w in route.waypoints if w.get('completed', False)) if route.waypoints else 0,
                           "percentage": int(sum(1 for w in route.waypoints if w.get('completed', False)) / len(
                               route.waypoints) * 100) if route.waypoints and len(route.waypoints) > 0 else 0}
    )


@routes.route('/<int:route_id>/add-status-update', methods=['POST'])
@login_required
def add_status_update(route_id):
    """
    Add status update to route (e.g., from driver)
    """
    route = Route.query.get_or_404(route_id)

    # Only driver assigned to this route can add updates
    if current_user.role != UserRole.DRIVER or not current_user.driver or current_user.driver.id != route.driver_id:
        flash('You do not have permission to add updates to this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    # Route must be in progress
    if route.status != RouteStatus.IN_PROGRESS:
        flash('You can only add updates to routes that are in progress.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    status_message = request.form.get('status_message', '')
    if not status_message:
        flash('Status update cannot be empty.', 'warning')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # In a real app, this would add to a status_updates table
        # For now we'll just add it to the route's log or waypoints
        if not route.waypoints:
            route.waypoints = []

        # Find active waypoint or add status to the route itself
        active_waypoint = None
        for i, waypoint in enumerate(route.waypoints):
            if not waypoint.get('completed', False):
                active_waypoint = i
                break

        if active_waypoint is not None:
            if 'status_updates' not in route.waypoints[active_waypoint]:
                route.waypoints[active_waypoint]['status_updates'] = []

            route.waypoints[active_waypoint]['status_updates'].append({
                'message': status_message,
                'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            })
        else:
            # Add to route status updates
            if not hasattr(route, 'status_updates'):
                route.status_updates = []

            # In a real app, this would use a proper model
            # For now, we'll update the route directly
            route.status_updates.append({
                'message': status_message,
                'timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            })

        db.session.commit()
        log_action(ActionType.UPDATE, f"Added status update to route {route.id}", db)
        flash('Status update added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding status update: {str(e)}', 'danger')

    return redirect(url_for('routes.view_route', route_id=route.id))


@routes.route('/<int:route_id>/waypoint/<int:waypoint_index>/complete', methods=['POST'])
@login_required
def complete_waypoint(route_id, waypoint_index):
    """
    Mark a waypoint as completed
    """
    route = Route.query.get_or_404(route_id)

    # Only driver assigned to this route can update waypoints
    if current_user.role != UserRole.DRIVER or not current_user.driver or current_user.driver.id != route.driver_id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'Permission denied'})
        flash('You do not have permission to update waypoints for this route.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    # Route must be in progress
    if route.status != RouteStatus.IN_PROGRESS:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'Route is not in progress'})
        flash('You can only update waypoints for routes that are in progress.', 'danger')
        return redirect(url_for('routes.view_route', route_id=route.id))

    try:
        # Check if waypoint exists
        if not route.waypoints or waypoint_index >= len(route.waypoints):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': 'Invalid waypoint'})
            flash('Invalid waypoint.', 'danger')
            return redirect(url_for('routes.view_route', route_id=route.id))

        # Mark waypoint as completed
        route.waypoints[waypoint_index]['completed'] = True
        route.waypoints[waypoint_index]['completion_time'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

        # Set next waypoint as active
        next_waypoint_index = None
        for i, waypoint in enumerate(route.waypoints):
            waypoint['active'] = False
            if i > waypoint_index and not waypoint.get('completed', False) and next_waypoint_index is None:
                next_waypoint_index = i

        if next_waypoint_index is not None:
            route.waypoints[next_waypoint_index]['active'] = True

        # Check if all waypoints are completed
        all_completed = all(w.get('completed', False) for w in route.waypoints)

        # Update response data
        response_data = {
            'success': True,
            'waypointIndex': waypoint_index,
            'allCompleted': all_completed,
            'nextWaypointIndex': next_waypoint_index
        }

        db.session.commit()
        log_action(ActionType.UPDATE, f"Completed waypoint {waypoint_index} for route {route.id}", db)

        # If AJAX request, return JSON response
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(response_data)

        flash('Waypoint completed successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error completing waypoint: {str(e)}', 'danger')

    return redirect(url_for('routes.view_route', route_id=route.id))


# Helper functions
def _can_access_route(route):
    """
    Check if current user can access a route
    """
    # Admin can access any route
    if current_user.role == UserRole.ADMIN:
        return True

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Must be in same company
    if route.company_id != company_id:
        return False

    # Drivers can only see their routes
    if current_user.role == UserRole.DRIVER and route.driver_id != current_user.driver.id:
        return False

    return True


def _can_edit_route(route):
    """
    Check if current user can edit a route
    """
    # Admin, company owner can edit
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and route.company_id == current_user.company_owner.company_id:
        return True

    # Manager, operator in same company can edit
    if current_user.role == UserRole.MANAGER and current_user.manager and route.company_id == current_user.manager.company_id:
        return True

    if current_user.role == UserRole.OPERATOR and current_user.operator and route.company_id == current_user.operator.company_id:
        return True

    # Driver can edit their own route if it's not completed
    if current_user.role == UserRole.DRIVER and current_user.driver and route.driver_id == current_user.driver.id and route.status != RouteStatus.COMPLETED:
        return True

    return False


def _can_delete_route(route):
    """
    Check if current user can delete a route
    """
    # Only admin, company owner, and manager can delete
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and route.company_id == current_user.company_owner.company_id:
        return True

    if current_user.role == UserRole.MANAGER and current_user.manager and route.company_id == current_user.manager.company_id:
        return True

    return False


def _can_cancel_route(route):
    """
    Check if current user can cancel a route
    """
    # Admin, company owner, manager can cancel
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and route.company_id == current_user.company_owner.company_id:
        return True

    if current_user.role == UserRole.MANAGER and current_user.manager and route.company_id == current_user.manager.company_id:
        return True

    # Operator in same company can cancel
    if current_user.role == UserRole.OPERATOR and current_user.operator and route.company_id == current_user.operator.company_id:
        return True

    # Driver can cancel their own route if it's not completed
    if current_user.role == UserRole.DRIVER and current_user.driver and route.driver_id == current_user.driver.id and route.status == RouteStatus.PLANNED:
        return True

    return False


@routes.route('/geocode-maps-url', methods=['POST'])
@login_required
def geocode_maps_url():
    """
    API endpoint to extract coordinates from Google Maps URL
    """
    data = request.json
    url = data.get('url', '')

    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'})

    result = extract_coordinates_from_maps_url(url)

    # Log the extraction attempt
    log_action(ActionType.VIEW, f"Extracted coordinates from Google Maps URL", db)

    return jsonify(result)


@routes.route('/process-short-url', methods=['POST'])
@login_required
def process_short_url():
    """
    Process a short Google Maps URL and extract coordinates
    """
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Invalid request format, expected JSON'}), 400

    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({'success': False, 'error': 'No URL provided'}), 400

    try:
        latitude, longitude = get_coordinates(url)

        if latitude is not None and longitude is not None:
            return jsonify({
                'success': True,
                'latitude': latitude,
                'longitude': longitude
            })
        else:
            return jsonify({'success': False, 'error': 'Could not extract coordinates from URL'}), 404

    except Exception as e:
        log_action(ActionType.ERROR, f"Error processing URL: {str(e)}", db)
        return jsonify({'success': False, 'error': str(e)}), 500


def get_coordinates(short_url):
    """
    Extract coordinates from a Google Maps URL by following redirects

    Args:
        short_url: Short or full Google Maps URL

    Returns:
        tuple: (latitude, longitude) or (None, None) if extraction failed
    """
    try:
        # Add timeout and user agent for better reliability
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(short_url, allow_redirects=True, timeout=10, headers=headers)
        full_url = response.url

        # Log for debugging
        current_app.logger.info(f"Processing URL: {short_url} -> {full_url}")

        # Try different patterns to extract coordinates

        # Pattern 1: /maps/search/lat,long
        match = re.search(r'/maps/search/([-+]?\d+\.\d+),\s*([-+]?\d+\.\d+)', full_url)
        if match:
            return float(match.group(1)), float(match.group(2))

        # Pattern 2: @lat,long
        match = re.search(r'@([-+]?\d+\.\d+),([-+]?\d+\.\d+)', full_url)
        if match:
            return float(match.group(1)), float(match.group(2))

        # Pattern 3: !3dlat!4dlong
        match = re.search(r'!3d([-+]?\d+\.\d+)!4d([-+]?\d+\.\d+)', full_url)
        if match:
            return float(match.group(1)), float(match.group(2))

        # Pattern 4: q=lat,long
        match = re.search(r'q=([-+]?\d+\.\d+),([-+]?\d+\.\d+)', full_url)
        if match:
            return float(match.group(1)), float(match.group(2))

        # If we reach here, we couldn't extract coordinates
        return None, None

    except requests.RequestException as e:
        current_app.logger.error(f"Error processing URL {short_url}: {e}")
        return None, None