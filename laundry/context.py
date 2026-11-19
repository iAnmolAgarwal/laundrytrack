from .services.schedule import counter_hours_label, weekdays_label


def laundry(request):
    """Values every template can use."""
    days = ""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, "profile", None)
        days = weekdays_label(profile.laundry_days if profile else None)
    return {"laundry_window": counter_hours_label(), "my_laundry_days": days}
