from django.db import models


class City(models.Model):
    """Offline US gazetteer used to geocode stations and user input."""

    name = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    name_key = models.CharField(max_length=128, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["state", "name_key"])]
        verbose_name_plural = "cities"

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    retail_price = models.FloatField(help_text="USD per gallon")
    latitude = models.FloatField()
    longitude = models.FloatField()

    class Meta:
        ordering = ["opis_id"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price:.3f}"
