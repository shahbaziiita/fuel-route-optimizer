from django.contrib import admin

from .models import City, FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("opis_id", "name", "city", "state", "retail_price")
    list_filter = ("state",)
    search_fields = ("name", "city", "address")


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "latitude", "longitude")
    list_filter = ("state",)
    search_fields = ("name",)
