from django.contrib import admin

from .models import Attempt, Question, Quiz, Response, SourceDocument


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("order", "question_type", "prompt", "choices", "correct_choices", "explanation", "points")
    readonly_fields = ("id",)


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "user", "char_count", "page_count", "created_at")
    search_fields = ("original_filename", "user__username")
    list_filter = ("created_at",)
    readonly_fields = ("extracted_text",)


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "difficulty", "question_count", "created_at")
    list_filter = ("status", "difficulty", "created_at")
    search_fields = ("title", "user__username")
    inlines = [QuestionInline]
    readonly_fields = ("error_message", "progress_done", "progress_total")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("quiz", "order", "question_type", "prompt", "points")
    list_filter = ("question_type",)


class ResponseInline(admin.TabularInline):
    model = Response
    extra = 0
    readonly_fields = ("question", "user_answer", "is_correct", "points_earned")


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ("quiz", "user", "score", "max_score", "percentage", "submitted_at")
    list_filter = ("submitted_at",)
    inlines = [ResponseInline]
