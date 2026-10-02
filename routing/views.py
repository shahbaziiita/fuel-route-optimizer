import json

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services.geocoding import LocationError
from .services.optimizer import NoFeasiblePlan
from .services.planner import plan_trip
from .services.routing_client import RoutingError


def _params(request):
    """Accept GET query params or a JSON / form POST body."""
    if request.method == "POST":
        if request.content_type == "application/json":
            try:
                data = json.loads(request.body or b"{}")
            except json.JSONDecodeError:
                raise ValueError("Request body is not valid JSON.")
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object.")
        else:
            data = request.POST
    else:
        data = request.GET

    start = (data.get("start") or "").strip()
    finish = (data.get("finish") or data.get("end") or "").strip()
    if not start or not finish:
        raise ValueError("Both 'start' and 'finish' are required, e.g. start=Dallas, TX&finish=Chicago, IL")

    start_tank = str(data.get("start_tank") or "empty").lower()
    if start_tank not in ("empty", "full"):
        raise ValueError("'start_tank' must be 'empty' or 'full'.")

    corridor = data.get("corridor_miles")
    if corridor not in (None, ""):
        try:
            corridor = float(corridor)
        except (TypeError, ValueError):
            raise ValueError("'corridor_miles' must be a number.")
        if not 0.5 <= corridor <= 50:
            raise ValueError("'corridor_miles' must be between 0.5 and 50.")
    else:
        corridor = None

    penalty = data.get("stop_penalty")
    if penalty not in (None, ""):
        try:
            penalty = float(penalty)
        except (TypeError, ValueError):
            raise ValueError("'stop_penalty' must be a number.")
        if not 0 <= penalty <= 500:
            raise ValueError("'stop_penalty' must be between 0 and 500.")
    else:
        penalty = None
    return start, finish, start_tank, corridor, penalty


def _run(request):
    """Returns (result, None) or (None, (status, error_payload))."""
    try:
        start, finish, start_tank, corridor, penalty = _params(request)
    except ValueError as exc:
        return None, (400, {"error": "bad_request", "detail": str(exc)})
    try:
        result = plan_trip(start, finish, start_tank=start_tank,
                           corridor_miles=corridor, stop_penalty=penalty)
    except LocationError as exc:
        return None, (400, {"error": "location_not_found", "detail": str(exc)})
    except RoutingError as exc:
        return None, (502, {"error": "routing_failed", "detail": str(exc)})
    except NoFeasiblePlan as exc:
        return None, (422, {"error": "no_feasible_fuel_plan", "detail": str(exc),
                            "stuck_at_mile": exc.stuck_at_mile})
    query = {"start": start, "finish": finish, "start_tank": start_tank}
    if corridor:
        query["corridor_miles"] = corridor
    if penalty is not None:
        query["stop_penalty"] = penalty
    result = {**result, "map_url": request.build_absolute_uri(
        reverse("route-map") + "?" + urlencode(query))}
    return result, None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def route_plan(request):
    """GET/POST /api/route/  ->  JSON trip plan."""
    result, err = _run(request)
    if err:
        return JsonResponse(err[1], status=err[0])
    return JsonResponse(result)


@require_http_methods(["GET"])
def route_map(request):
    """GET /api/route/map/  ->  interactive Leaflet map of the same plan.
    Served from cache, so it does not call the routing API again."""
    result, err = _run(request)
    if err:
        return render(request, "routing/map.html", {"error": err[1]["detail"]}, status=err[0])
    return render(request, "routing/map.html", {"plan": result})
