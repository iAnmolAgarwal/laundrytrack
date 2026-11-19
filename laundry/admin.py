from django.contrib import admin

from .models import DailyUsage, LaundryItem, LaundrySlip, Profile


class LaundryItemInline(admin.TabularInline):
    model = LaundryItem
    extra = 0


@admin.register(LaundrySlip)
class LaundrySlipAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "student_name", "room_no", "drop_date", "pickup_date", "status", "total_items", "email_sent")
    list_filter = ("status", "drop_date", "email_sent")
    search_fields = ("student_name", "room_no", "slip_no", "user__username", "user__email")
    date_hierarchy = "drop_date"
    inlines = [LaundryItemInline]


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "enrollment_no", "hostel", "room_no", "laundry_days")
    search_fields = ("user__username", "hostel", "room_no")


@admin.register(DailyUsage)
class DailyUsageAdmin(admin.ModelAdmin):
    list_display = ("user", "day", "uploads", "emails")
    list_filter = ("day",)
