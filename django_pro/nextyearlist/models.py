from django.db import models


class NextYearPlacement(models.Model):
    """Maps a student to their next-year school enrollment.
    Data is loaded externally (ETL/DB push). This model is read-only in the app.
    """
    ssid = models.CharField(max_length=20, db_index=True)
    student_id = models.CharField(max_length=20, blank=True, db_index=True)
    stu_annual_key = models.CharField(max_length=32, blank=True, db_index=True)
    school_year = models.CharField(max_length=20, db_index=True)
    next_year_school_state_id = models.CharField(max_length=20, db_index=True)
    next_year_school_name = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'nextyearlist_placement'
        unique_together = ('ssid', 'school_year')
        indexes = [
            models.Index(fields=['school_year', 'next_year_school_state_id']),
        ]
        verbose_name = 'Next Year Placement'
        verbose_name_plural = 'Next Year Placements'

    def __str__(self):
        return f'{self.ssid} → {self.next_year_school_name} ({self.school_year})'
